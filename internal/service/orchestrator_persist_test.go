package service

import (
	"context"
	"errors"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
)

type persistConversationStub struct {
	repository.ConversationRepository
	updated bool
}

func (s *persistConversationStub) UpdateConversationHistory(context.Context, string, []model.ChatMessage) error {
	s.updated = true
	return nil
}

type persistMemoryStub struct {
	MemoryService
	called bool
	err    error
}

func (s *persistMemoryStub) PersistInteraction(context.Context, *model.User, string, []model.ChatMessage, string, string) error {
	s.called = true
	return s.err
}

func persistRequest(answer string) *model.OrchestratorPersistRequest {
	return &model.OrchestratorPersistRequest{User: model.OrchestratorUser{ID: 7}, ConversationID: "conversation-7", Query: "question", Answer: answer}
}

func TestPersistTurnWaitsForDurableMemoryAndReturnsFailure(t *testing.T) {
	conversations := &persistConversationStub{}
	memory := &persistMemoryStub{err: errors.New("database unavailable")}
	service := &orchestratorSupportService{conversationRepo: conversations, memoryService: memory}
	err := service.PersistTurn(context.Background(), persistRequest("completed answer"))
	if err == nil || !memory.called || !conversations.updated {
		t.Fatalf("err=%v memory=%v history=%v", err, memory.called, conversations.updated)
	}
}

func TestPersistTurnSkipsFailedOrCancelledCompletion(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	cases := map[string]struct {
		ctx    context.Context
		answer string
	}{
		"failed":    {context.Background(), ""},
		"cancelled": {cancelled, "partial"},
	}
	for name, testCase := range cases {
		t.Run(name, func(t *testing.T) {
			memory := &persistMemoryStub{}
			service := &orchestratorSupportService{conversationRepo: &persistConversationStub{}, memoryService: memory}
			_ = service.PersistTurn(testCase.ctx, persistRequest(testCase.answer))
			if memory.called {
				t.Fatal("memory persisted for non-completed turn")
			}
		})
	}
}
