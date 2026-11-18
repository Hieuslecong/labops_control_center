package main

import (
	"fmt"
	"os"

	"github.com/Hieuslecong/labops_control_center/go/internal/gitaiotcli"
)

func main() {
	fmt.Fprintln(os.Stderr, "[DEPRECATED] Lệnh 'labops' đã ngừng khuyến nghị và sẽ bị loại bỏ trong bản phát hành tới; vui lòng sử dụng 'gitaiot'.")
	os.Exit(gitaiotcli.Run(os.Args[1:], "labops", os.Stdout, os.Stderr))
}
