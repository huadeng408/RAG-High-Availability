package main

import (
	"bytes"
	"context"
	"crypto/md5"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"pai-smart-go/internal/config"
	"pai-smart-go/internal/handler"
	"pai-smart-go/internal/middleware"
	"pai-smart-go/internal/model"
	"pai-smart-go/internal/pipeline"
	"pai-smart-go/internal/repository"
	"pai-smart-go/internal/service"
	"pai-smart-go/pkg/database"
	"pai-smart-go/pkg/embedding"
	"pai-smart-go/pkg/es"
	"pai-smart-go/pkg/kafka"
	"pai-smart-go/pkg/llm"
	"pai-smart-go/pkg/log"
	"pai-smart-go/pkg/storage"
	"pai-smart-go/pkg/tika"
	"pai-smart-go/pkg/token"

	"github.com/gin-gonic/gin"
)

func main() {
	config.Init("./configs/config.yaml")
	cfg := config.Conf

	log.Init(cfg.Log.Level, cfg.Log.Format, cfg.Log.OutputPath)
	defer log.Sync()

	database.InitMySQL(cfg.Database.MySQL.DSN)
	database.InitRedis(cfg.Database.Redis.Addr, cfg.Database.Redis.Password, cfg.Database.Redis.DB)
	if err := database.DB.AutoMigrate(&model.PipelineTask{}); err != nil {
		log.Errorf("failed to migrate pipeline_task table: %v", err)
		return
	}

	storage.InitMinIO(cfg.MinIO)
	if err := es.InitES(cfg.Elasticsearch); err != nil {
		log.Errorf("failed to init elasticsearch: %v", err)
		return
	}
	kafka.InitProducer(cfg.Kafka)

	userRepository := repository.NewUserRepository(database.DB)
	orgTagRepo := repository.NewOrgTagRepository(database.DB)
	uploadRepo := repository.NewUploadRepository(database.DB, database.RDB)
	conversationRepo := repository.NewConversationRepository(database.RDB)
	docVectorRepo := repository.NewDocumentVectorRepository(database.DB)
	pipelineTaskRepo := repository.NewPipelineTaskRepository(database.DB)

	jwtManager := token.NewJWTManager(cfg.JWT.Secret, cfg.JWT.AccessTokenExpireHours, cfg.JWT.RefreshTokenExpireDays)
	tikaClient := tika.NewClient(cfg.Tika)
	embeddingClient := embedding.NewClient(cfg.Embedding)
	llmClient := llm.NewClient(cfg.LLM)

	userService := service.NewUserService(userRepository, orgTagRepo, jwtManager)
	adminService := service.NewAdminService(orgTagRepo, userRepository, conversationRepo, pipelineTaskRepo, uploadRepo)
	uploadService := service.NewUploadService(uploadRepo, userRepository, cfg.MinIO)
	documentService := service.NewDocumentService(uploadRepo, userRepository, orgTagRepo, cfg.MinIO, tikaClient)
	searchService := service.NewSearchService(embeddingClient, es.ESClient, userService, uploadRepo)
	conversationService := service.NewConversationService(conversationRepo)
	chatService := service.NewChatService(searchService, llmClient, conversationRepo)

	processor := pipeline.NewProcessor(
		tikaClient,
		embeddingClient,
		cfg.Elasticsearch,
		cfg.MinIO,
		cfg.Embedding,
		cfg.Kafka,
		uploadRepo,
		docVectorRepo,
	)
	go kafka.StartPipelineConsumers(cfg.Kafka, processor, pipelineTaskRepo)

	initCtx, cancelInit := context.WithCancel(context.Background())
	defer cancelInit()
	go initSeedFiles(initCtx, "initfile", userRepository, uploadService)

	gin.SetMode(cfg.Server.Mode)
	r := gin.New()

	r.Use(func(c *gin.Context) {
		if len(c.Request.URL.Path) >= len("/api/") && c.Request.URL.Path[:len("/api/")] == "/api/" {
			if c.Writer.Header().Get("Content-Type") == "" {
				c.Writer.Header().Set("Content-Type", "application/json; charset=utf-8")
			}
		}
		c.Next()
	})
	r.Use(middleware.RequestLogger(), gin.Recovery())

	apiV1 := r.Group("/api/v1")
	{
		auth := apiV1.Group("/auth")
		{
			auth.POST("/refreshToken", handler.NewAuthHandler(userService).RefreshToken)
		}

		users := apiV1.Group("/users")
		{
			users.POST("/register", handler.NewUserHandler(userService).Register)
			users.POST("/login", handler.NewUserHandler(userService).Login)

			authed := users.Group("/")
			authed.Use(middleware.AuthMiddleware(jwtManager, userService))
			{
				authed.GET("/me", handler.NewUserHandler(userService).GetProfile)
				authed.POST("/logout", handler.NewUserHandler(userService).Logout)
				authed.PUT("/primary-org", handler.NewUserHandler(userService).SetPrimaryOrg)
				authed.GET("/org-tags", handler.NewUserHandler(userService).GetUserOrgTags)
			}
		}

		upload := apiV1.Group("/upload")
		upload.Use(middleware.AuthMiddleware(jwtManager, userService))
		{
			h := handler.NewUploadHandler(uploadService)
			upload.POST("/check", h.CheckFile)
			upload.POST("/chunk", h.UploadChunk)
			upload.POST("/merge", h.MergeChunks)
			upload.GET("/status", h.GetUploadStatus)
			upload.GET("/supported-types", h.GetSupportedFileTypes)
			upload.POST("/fast-upload", h.FastUpload)
		}

		documents := apiV1.Group("/documents")
		documents.Use(middleware.AuthMiddleware(jwtManager, userService))
		{
			dh := handler.NewDocumentHandler(documentService, userService)
			documents.GET("/accessible", dh.ListAccessibleFiles)
			documents.GET("/uploads", dh.ListUploadedFiles)
			documents.DELETE("/:fileMd5", dh.DeleteDocument)
			documents.GET("/download", dh.GenerateDownloadURL)
			documents.GET("/preview", dh.PreviewFile)
		}

		search := apiV1.Group("/search")
		search.Use(middleware.AuthMiddleware(jwtManager, userService))
		{
			search.GET("/hybrid", handler.NewSearchHandler(searchService).HybridSearch)
		}

		conversation := apiV1.Group("/users/conversation")
		conversation.Use(middleware.AuthMiddleware(jwtManager, userService))
		{
			conversation.GET("", handler.NewConversationHandler(conversationService).GetConversations)
		}

		chatGroup := apiV1.Group("/chat")
		chatHandler := handler.NewChatHandler(chatService, userService, jwtManager)
		{
			chatGroup.GET("/websocket-token", chatHandler.GetWebsocketStopToken)
		}
		r.GET("/chat/:token", chatHandler.Handle)

		admin := apiV1.Group("/admin")
		admin.Use(middleware.AuthMiddleware(jwtManager, userService), middleware.AdminAuthMiddleware())
		{
			ah := handler.NewAdminHandler(adminService, userService)
			admin.GET("/users/list", ah.ListUsers)
			admin.PUT("/users/:userId/org-tags", ah.AssignOrgTagsToUser)
			admin.GET("/conversation", ah.GetAllConversations)
			admin.POST("/pipeline/replay", ah.ReplayPipelineTask)

			orgTags := admin.Group("/org-tags")
			{
				orgTags.POST("", ah.CreateOrganizationTag)
				orgTags.GET("", ah.ListOrganizationTags)
				orgTags.GET("/tree", ah.GetOrganizationTagTree)
				orgTags.PUT("/:id", ah.UpdateOrganizationTag)
				orgTags.DELETE("/:id", ah.DeleteOrganizationTag)
			}
		}
	}

	srv := &http.Server{Addr: fmt.Sprintf(":%s", cfg.Server.Port), Handler: r}

	go func() {
		log.Infof("server started on %s", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server failed: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Info("shutdown signal received")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("failed to shutdown server: %v", err)
	}
	log.Info("server stopped")
}

func initSeedFiles(ctx context.Context, dir string, userRepo repository.UserRepository, uploadSvc service.UploadService) {
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		log.Infof("initSeedFiles: directory '%s' not found, skipped", dir)
		return
	}

	var ownerUserID uint
	var ownerOrg string
	if admin, err := userRepo.FindByUsername("admin"); err == nil && admin != nil {
		ownerUserID = admin.ID
		ownerOrg = admin.PrimaryOrg
	} else {
		users, err := userRepo.FindAll()
		if err != nil || len(users) == 0 {
			log.Warnf("initSeedFiles: no available user, skipped")
			return
		}
		ownerUserID = users[0].ID
		ownerOrg = users[0].PrimaryOrg
	}

	walkErr := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}

		f, err := os.Open(path)
		if err != nil {
			log.Warnf("initSeedFiles: failed to open file: %s, err=%v", path, err)
			return nil
		}
		h := md5.New()
		size, copyErr := io.Copy(h, f)
		_ = f.Close()
		if copyErr != nil {
			log.Warnf("initSeedFiles: failed to read file: %s, err=%v", path, copyErr)
			return nil
		}
		fileMD5 := fmt.Sprintf("%x", h.Sum(nil))
		fileName := info.Name()

		if uploaded, ferr := uploadSvc.FastUpload(ctx, fileMD5, ownerUserID); ferr == nil && uploaded {
			log.Infof("initSeedFiles: skip existing file %s (md5=%s)", fileName, fileMD5)
			return nil
		}

		const chunkSize int64 = 5 * 1024 * 1024
		totalChunks := int(math.Ceil(float64(size) / float64(chunkSize)))
		if totalChunks == 0 {
			return nil
		}

		file, err := os.Open(path)
		if err != nil {
			log.Warnf("initSeedFiles: failed to reopen file: %s, err=%v", path, err)
			return nil
		}
		defer file.Close()

		for chunkIndex := 0; chunkIndex < totalChunks; chunkIndex++ {
			offset := int64(chunkIndex) * chunkSize
			if _, err := file.Seek(offset, io.SeekStart); err != nil {
				log.Warnf("initSeedFiles: seek failed: %s, chunk=%d, err=%v", path, chunkIndex, err)
				return nil
			}
			toRead := chunkSize
			if offset+toRead > size {
				toRead = size - offset
			}
			buf := make([]byte, toRead)
			if _, err := io.ReadFull(file, buf); err != nil {
				log.Warnf("initSeedFiles: read chunk failed: %s, chunk=%d, err=%v", path, chunkIndex, err)
				return nil
			}
			chunkMD5 := fmt.Sprintf("%x", md5.Sum(buf))
			cf := &chunkFile{Reader: bytes.NewReader(buf)}
			if _, _, err := uploadSvc.UploadChunk(ctx, fileMD5, fileName, size, chunkIndex, cf, chunkMD5, ownerUserID, ownerOrg, true); err != nil {
				log.Warnf("initSeedFiles: upload chunk failed: %s, chunk=%d, err=%v", path, chunkIndex, err)
				return nil
			}
		}

		if _, err := uploadSvc.MergeChunks(ctx, fileMD5, fileName, ownerUserID); err != nil {
			log.Warnf("initSeedFiles: merge failed: %s, err=%v", path, err)
			return nil
		}
		log.Infof("initSeedFiles: imported and triggered pipeline: %s", fileName)
		return nil
	})
	if walkErr != nil {
		log.Warnf("initSeedFiles: walk directory error: %v", walkErr)
	}
}

type chunkFile struct{ Reader *bytes.Reader }

func (c *chunkFile) Read(p []byte) (int, error)              { return c.Reader.Read(p) }
func (c *chunkFile) ReadAt(p []byte, off int64) (int, error) { return c.Reader.ReadAt(p, off) }
func (c *chunkFile) Seek(offset int64, whence int) (int64, error) {
	return c.Reader.Seek(offset, whence)
}
func (c *chunkFile) Close() error { return nil }
