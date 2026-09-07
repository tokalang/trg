package main

import (
	"fmt"
	"sync"
)

type WorkerPool struct {
	workers int
	wg      sync.WaitGroup
}

func NewWorkerPool(n int) *WorkerPool {
	// Initialize worker pool with n workers
	return &WorkerPool{workers: n}
}

func (p *WorkerPool) Run() {
	rawSQL := `SELECT id, name FROM users WHERE role = 'admin'`
	fmt.Printf("Running pool (%d workers), query: %s\n", p.workers, rawSQL)
}
