// Package service contains business logic.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/kafka"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"

	"gorm.io/gorm"
)

// UserListResponse describes the user list response payload.
type UserListResponse struct {
	Content       []UserDetailResponse `json:"content"`
	TotalElements int64                `json:"totalElements"`
	TotalPages    int                  `json:"totalPages"`
	Size          int                  `json:"size"`
	Number        int                  `json:"number"`
}

// UserDetailResponse describes the user detail response payload.
type UserDetailResponse struct {
	UserID     uint            `json:"userId"`
	Username   string          `json:"username"`
	Role       string          `json:"role"`
	OrgTags    []OrgTagDetail  `json:"orgTags"`
	PrimaryOrg string          `json:"primaryOrg"`
	Status     int             `json:"status"`
	CreatedAt  model.LocalTime `json:"createdAt"`
}

// OrgTagDetail represents an org tag detail.
type OrgTagDetail struct {
	TagID string `json:"tagId"`
	Name  string `json:"name"`
}

// AdminService defines admin operations.
type AdminService interface {
	CreateOrganizationTag(tagID, name, description, parentTag string, creator *model.User) (*model.OrganizationTag, error)
	ListOrganizationTags() ([]model.OrganizationTag, error)
	GetOrganizationTagTree() ([]*model.OrganizationTagNode, error)
	UpdateOrganizationTag(tagID string, name, description, parentTag string) (*model.OrganizationTag, error)
	DeleteOrganizationTag(tagID string) error
	AssignOrgTagsToUser(userID uint, orgTags []string) error
	ListUsers(page, size int) (*UserListResponse, error)
	GetAllConversations(ctx context.Context, userID *uint, startTime, endTime *time.Time) ([]map[string]interface{}, error)
	ReplayPipelineTask(fileMD5 string, stage tasks.Stage) (*PipelineReplayResult, error)
}

// PipelineReplayResult identifies the durable dead-letter tasks accepted for replay.
type PipelineReplayResult struct {
	FileMD5       string      `json:"fileMd5"`
	Stage         tasks.Stage `json:"stage"`
	ReplayedTasks int         `json:"replayedTasks"`
	MessageIDs    []string    `json:"messageIds"`
}

// adminService implements admin operations.
type adminService struct {
	orgTagRepo       repository.OrgTagRepository
	userRepo         repository.UserRepository
	conversationRepo repository.ConversationRepository
	pipelineTaskRepo repository.PipelineTaskRepository
	uploadRepo       repository.UploadRepository
	produceTask      func(tasks.FileProcessingTask) error
}

// NewAdminService creates an admin service.
func NewAdminService(
	orgTagRepo repository.OrgTagRepository,
	userRepo repository.UserRepository,
	conversationRepo repository.ConversationRepository,
	pipelineTaskRepo repository.PipelineTaskRepository,
	uploadRepo repository.UploadRepository,
) AdminService {
	return &adminService{
		orgTagRepo:       orgTagRepo,
		userRepo:         userRepo,
		conversationRepo: conversationRepo,
		pipelineTaskRepo: pipelineTaskRepo,
		uploadRepo:       uploadRepo,
		produceTask:      kafka.ProduceTask,
	}
}

// CreateOrganizationTag creates organization tag.
func (s *adminService) CreateOrganizationTag(tagID, name, description, parentTag string, creator *model.User) (*model.OrganizationTag, error) {
	tagID = strings.TrimSpace(tagID)
	parentTag = strings.TrimSpace(parentTag)
	if tagID == "" {
		return nil, errors.New("tagID cannot be empty")
	}
	if strings.Contains(tagID, ",") {
		return nil, errors.New("tagID cannot contain comma")
	}
	if parentTag != "" && strings.Contains(parentTag, ",") {
		return nil, errors.New("parentTag cannot contain comma")
	}

	if _, err := s.orgTagRepo.FindByID(tagID); err == nil {
		return nil, errors.New("tagID already exists")
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, err
	}

	tag := &model.OrganizationTag{
		TagID:       tagID,
		Name:        name,
		Description: description,
		CreatedBy:   creator.ID,
	}
	if parentTag != "" {
		tag.ParentTag = &parentTag
	}
	if err := s.orgTagRepo.Create(tag); err != nil {
		return nil, err
	}
	return tag, nil
}

// ListOrganizationTags lists organization tags.
func (s *adminService) ListOrganizationTags() ([]model.OrganizationTag, error) {
	return s.orgTagRepo.FindAll()
}

// GetOrganizationTagTree returns organization tag tree.
func (s *adminService) GetOrganizationTagTree() ([]*model.OrganizationTagNode, error) {
	tags, err := s.orgTagRepo.FindAll()
	if err != nil {
		return nil, err
	}

	nodes := make(map[string]*model.OrganizationTagNode, len(tags))
	for _, tag := range tags {
		tagCopy := tag
		nodes[tag.TagID] = &model.OrganizationTagNode{
			TagID:       tagCopy.TagID,
			Name:        tagCopy.Name,
			Description: tagCopy.Description,
			ParentTag:   tagCopy.ParentTag,
			Children:    []*model.OrganizationTagNode{},
		}
	}

	tree := make([]*model.OrganizationTagNode, 0)
	for _, node := range nodes {
		if node.ParentTag != nil && *node.ParentTag != "" {
			if parent, ok := nodes[*node.ParentTag]; ok {
				parent.Children = append(parent.Children, node)
				continue
			}
		}
		tree = append(tree, node)
	}
	return tree, nil
}

// UpdateOrganizationTag updates organization tag.
func (s *adminService) UpdateOrganizationTag(tagID string, name, description, parentTag string) (*model.OrganizationTag, error) {
	tag, err := s.orgTagRepo.FindByID(tagID)
	if err != nil {
		return nil, errors.New("tag not found")
	}
	tag.Name = name
	tag.Description = description
	if strings.TrimSpace(parentTag) == "" {
		tag.ParentTag = nil
	} else {
		tag.ParentTag = &parentTag
	}
	if err := s.orgTagRepo.Update(tag); err != nil {
		return nil, err
	}
	return tag, nil
}

// DeleteOrganizationTag deletes organization tag.
func (s *adminService) DeleteOrganizationTag(tagID string) error {
	return s.orgTagRepo.Delete(tagID)
}

// AssignOrgTagsToUser handles assign org tags to user.
func (s *adminService) AssignOrgTagsToUser(userID uint, orgTags []string) error {
	user, err := s.userRepo.FindByID(userID)
	if err != nil {
		return err
	}

	validated := make([]string, 0, len(orgTags))
	seen := make(map[string]struct{}, len(orgTags))
	for _, rawTag := range orgTags {
		tagID := strings.TrimSpace(rawTag)
		if tagID == "" {
			continue
		}
		if strings.Contains(tagID, ",") {
			return fmt.Errorf("invalid org tag id: %s", tagID)
		}
		if _, ok := seen[tagID]; ok {
			continue
		}
		if _, err := s.orgTagRepo.FindByID(tagID); err != nil {
			return fmt.Errorf("org tag not found: %s", tagID)
		}
		seen[tagID] = struct{}{}
		validated = append(validated, tagID)
	}
	if len(validated) == 0 {
		return errors.New("orgTags cannot be empty")
	}

	user.OrgTags = strings.Join(validated, ",")
	if user.PrimaryOrg == "" || !containsTag(validated, user.PrimaryOrg) {
		user.PrimaryOrg = validated[0]
	}
	return s.userRepo.Update(user)
}

// ListUsers lists users.
func (s *adminService) ListUsers(page, size int) (*UserListResponse, error) {
	if page <= 0 {
		page = 1
	}
	if size <= 0 {
		size = 10
	}

	offset := (page - 1) * size
	users, total, err := s.userRepo.FindWithPagination(offset, size)
	if err != nil {
		return nil, err
	}

	userResponses := make([]UserDetailResponse, 0, len(users))
	for _, u := range users {
		orgTagDetails := make([]OrgTagDetail, 0)
		if u.OrgTags != "" {
			tagIDs := strings.Split(u.OrgTags, ",")
			for _, tagID := range tagIDs {
				tag, err := s.orgTagRepo.FindByID(tagID)
				if err != nil {
					continue
				}
				orgTagDetails = append(orgTagDetails, OrgTagDetail{TagID: tag.TagID, Name: tag.Name})
			}
		}

		status := 1
		if u.Role == "ADMIN" {
			status = 0
		}

		userResponses = append(userResponses, UserDetailResponse{
			UserID:     u.ID,
			Username:   u.Username,
			Role:       u.Role,
			OrgTags:    orgTagDetails,
			PrimaryOrg: u.PrimaryOrg,
			Status:     status,
			CreatedAt:  model.LocalTime(u.CreatedAt),
		})
	}

	totalPages := 0
	if total > 0 {
		totalPages = (int(total) + size - 1) / size
	}
	return &UserListResponse{
		Content:       userResponses,
		TotalElements: total,
		TotalPages:    totalPages,
		Size:          size,
		Number:        page,
	}, nil
}

// GetAllConversations returns all conversations.
func (s *adminService) GetAllConversations(ctx context.Context, userID *uint, startTime, endTime *time.Time) ([]map[string]interface{}, error) {
	if userID != nil {
		user, err := s.userRepo.FindByID(*userID)
		if err != nil {
			return nil, errors.New("user not found")
		}
		return s.getConversationsForUser(ctx, user, startTime, endTime)
	}

	mappings, err := s.conversationRepo.GetAllUserConversationMappings(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get user conversation mappings: %w", err)
	}

	allConversations := make([]map[string]interface{}, 0)
	for uid := range mappings {
		user, err := s.userRepo.FindByID(uid)
		if err != nil {
			continue
		}
		userConversations, err := s.getConversationsForUser(ctx, user, startTime, endTime)
		if err != nil {
			continue
		}
		allConversations = append(allConversations, userConversations...)
	}
	return allConversations, nil
}

// getConversationsForUser returns conversations for user.
func (s *adminService) getConversationsForUser(ctx context.Context, user *model.User, startTime, endTime *time.Time) ([]map[string]interface{}, error) {
	conversationID, err := s.conversationRepo.GetOrCreateConversationID(ctx, user.ID)
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) || err.Error() == "redis: nil" {
			return []map[string]interface{}{}, nil
		}
		return nil, fmt.Errorf("failed to get conversation id: %w", err)
	}

	history, err := s.conversationRepo.GetConversationHistory(ctx, conversationID)
	if err != nil {
		return nil, fmt.Errorf("failed to get conversation history: %w", err)
	}

	result := make([]map[string]interface{}, 0, len(history))
	for _, msg := range history {
		if startTime != nil && msg.Timestamp.Before(*startTime) {
			continue
		}
		if endTime != nil && msg.Timestamp.After(*endTime) {
			continue
		}
		result = append(result, map[string]interface{}{
			"username":  user.Username,
			"role":      msg.Role,
			"content":   msg.Content,
			"timestamp": msg.Timestamp.Format("2006-01-02T15:04:05"),
		})
	}
	return result, nil
}

// ReplayPipelineTask handles replay pipeline task.
func (s *adminService) ReplayPipelineTask(fileMD5 string, stage tasks.Stage) (*PipelineReplayResult, error) {
	fileMD5 = strings.TrimSpace(fileMD5)
	if fileMD5 == "" {
		return nil, errors.New("fileMd5 cannot be empty")
	}
	if stage == "" {
		stage = tasks.StageParse
	}
	if stage != tasks.StageParse && stage != tasks.StageChunk && stage != tasks.StageEmbed && stage != tasks.StageIndex {
		return nil, fmt.Errorf("unsupported stage: %s", stage)
	}

	uploadRecord, err := s.uploadRepo.GetFileUploadRecordByMD5(fileMD5)
	if err != nil {
		return nil, err
	}
	failedTasks, err := s.pipelineTaskRepo.ListFailedByFile(fileMD5)
	if err != nil {
		return nil, err
	}
	result := &PipelineReplayResult{FileMD5: fileMD5, Stage: stage, MessageIDs: []string{}}
	producer := s.produceTask
	if producer == nil {
		producer = kafka.ProduceTask
	}
	for _, failed := range failedTasks {
		if failed.Stage != string(stage) || failed.DLQMessageID == "" || failed.DLQPayload == "" {
			continue
		}
		var task tasks.FileProcessingTask
		if err := json.Unmarshal([]byte(failed.DLQPayload), &task); err != nil {
			return result, fmt.Errorf("decode DLQ payload %s: %w", failed.DLQMessageID, err)
		}
		task.FileMD5 = uploadRecord.FileMD5
		task.FileName = uploadRecord.FileName
		task.UserID = uploadRecord.UserID
		task.OrgTag = uploadRecord.OrgTag
		task.IsPublic = uploadRecord.IsPublic
		task.DocumentVersion = failed.DocumentVersion
		task.WindowID = failed.WindowID
		task.Stage = stage
		task.LastError = ""
		task.Attempt = 0
		task.DLQID = ""

		if err := s.pipelineTaskRepo.ResetForReplayByKey(
			fileMD5, failed.DocumentVersion, failed.Stage, failed.WindowID,
		); err != nil {
			return result, err
		}
		if err := producer(task); err != nil {
			restoreErr := s.pipelineTaskRepo.MarkDeadLetterByKey(
				fileMD5,
				failed.DocumentVersion,
				failed.Stage,
				failed.WindowID,
				"replay publish failed: "+err.Error(),
				failed.DLQPayload,
				failed.DLQMessageID,
			)
			if restoreErr != nil {
				return result, fmt.Errorf("publish replay: %w; restore dead letter: %v", err, restoreErr)
			}
			return result, fmt.Errorf("publish replay: %w", err)
		}
		result.ReplayedTasks++
		result.MessageIDs = append(result.MessageIDs, failed.DLQMessageID)
	}
	if result.ReplayedTasks == 0 {
		return nil, fmt.Errorf("no replayable %s dead-letter task found for file %s", stage, fileMD5)
	}
	return result, nil
}

// containsTag reports whether tag is present.
func containsTag(tags []string, target string) bool {
	for _, tag := range tags {
		if tag == target {
			return true
		}
	}
	return false
}
