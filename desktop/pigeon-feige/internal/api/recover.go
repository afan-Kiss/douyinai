package api

import "net/http"

func recoverHTTP(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				logPanic("http:"+r.URL.Path, rec)
				writeJSON(w, 500, map[string]any{"ok": false, "error": "internal error"})
			}
		}()
		next.ServeHTTP(w, r)
	})
}
