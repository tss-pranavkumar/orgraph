// Minimal Go HTTP fixture covering all three route-registration patterns
// orgraph detects: chi (capitalised method name), gin (uppercase verb), and
// stdlib net/http. Used by tests/test_scip.py::test_go_entry_points.

package main

import (
	"net/http"
)

// chi-style: r.Get("/users/{id}", getUser)
func getUser(w http.ResponseWriter, r *http.Request) {
	_, _ = w.Write([]byte("user"))
}

// gin-style: r.POST("/items", createItem)
func createItem(w http.ResponseWriter, r *http.Request) {
	_, _ = w.Write([]byte("created"))
}

// stdlib: http.HandleFunc("/health", healthz)
func healthz(w http.ResponseWriter, r *http.Request) {
	_, _ = w.Write([]byte("ok"))
}

// chi/gin generic dispatcher: r.Method("PUT", "/x", replaceItem)
func replaceItem(w http.ResponseWriter, r *http.Request) {
	_, _ = w.Write([]byte("replaced"))
}

// Plain helper — must NOT receive http_method or http_path.
func formatGreeting(name string) string {
	return "hello " + name
}

type Server struct{}

func registerRoutes(srv *Server) {
	// One of each framework's idiomatic registration shape.
	r := newRouter()
	r.Get("/users/{id}", getUser)         // chi
	r.POST("/items", createItem)          // gin
	r.Method("PUT", "/items/{id}", replaceItem) // chi generic
	http.HandleFunc("/health", healthz)   // stdlib
}

type router struct{}

func (r *router) Get(path string, h http.HandlerFunc)                    {}
func (r *router) POST(path string, h http.HandlerFunc)                   {}
func (r *router) Method(verb, path string, h http.HandlerFunc)           {}

func newRouter() *router { return &router{} }

func main() {
	registerRoutes(&Server{})
}
