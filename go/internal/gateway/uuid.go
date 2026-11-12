package gateway

import (
	"crypto/rand"
	"fmt"
)

// newUUID tạo một chuỗi UUID v4 chuẩn tuân thủ RFC 4122 không phụ thuộc thư viện ngoài
func newUUID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant RFC 4122
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

