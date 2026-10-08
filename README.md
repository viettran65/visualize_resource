# Server Monitor

Web giám sát tài nguyên **máy chủ Linux theo thời gian thực**, chỉ có hai màn hình **Dashboard** và **Performance**. Giao diện tối, biểu đồ lịch sử 60 giây, không cần Node.js, npm, Docker, database hay bước build frontend.

## Chạy

Trên máy chủ đã có `uv`, trong thư mục repo, chỉ cần:

```bash
uv sync
uv run python main.py
```

Mở **http://localhost:8859** nếu trình duyệt ở cùng máy, hoặc **http://IP-SERVER:8859** từ máy khác. Chương trình in các địa chỉ truy cập khi khởi động. Mặc định lắng nghe `0.0.0.0:8859`; cổng 8859 cần được cho phép trong firewall của server nếu truy cập qua mạng. Dừng bằng `Ctrl+C`.

`uv sync` tạo môi trường Python và cài thư viện theo `uv.lock`. Python 3.12 được chọn qua `.python-version`; uv có thể tự tải Python khi máy chưa có phiên bản phù hợp. Không cần kích hoạt virtualenv thủ công. Tham khảo [tài liệu uv](https://docs.astral.sh/uv/guides/projects/).

## Dữ liệu hiển thị

- **CPU:** mức sử dụng tổng và từng lõi, tên CPU, số lõi vật lý/luồng, tần số, load average và nhiệt độ nếu hệ điều hành cung cấp.
- **RAM:** bộ nhớ đã dùng/còn khả dụng, cache, buffers và swap.
- **Ổ đĩa:** dung lượng từng phân vùng, đọc/ghi mỗi giây và mức bận của thiết bị khi có số liệu.
- **Mạng:** lưu lượng nhận/gửi mỗi giây, bộ đếm tích lũy, địa chỉ và trạng thái từng interface.
- **GPU NVIDIA:** tên, utilization, VRAM, nhiệt độ và công suất khi `nvidia-smi` hoạt động. Không cần cài thư viện CUDA cho ứng dụng. GPU khác hoặc máy không có GPU sẽ hiển thị trạng thái chưa hỗ trợ/không khả dụng.
- **Server:** hostname, hệ điều hành/kernel, kiến trúc, uptime, cảm biến có sẵn và các tiến trình dùng nhiều tài nguyên trong Dashboard.

Backend lấy mẫu một lần mỗi giây và đẩy đến mọi trình duyệt qua **Server-Sent Events (SSE)**. Tốc độ đọc/ghi và mạng được tính từ chênh lệch bộ đếm theo khoảng thời gian thực giữa hai mẫu. Lịch sử chỉ lưu trong RAM, tối đa 60 giây và bắt đầu tích lũy từ lúc chạy server. Các trình duyệt chia sẻ cùng dữ liệu, không tạo thêm tác vụ lấy mẫu.

Dữ liệu là của **máy chạy `main.py`**, không phải máy mở trình duyệt. Nếu chạy trong container, ứng dụng đọc `/proc` và các filesystem mà container nhìn thấy; CPU/RAM có thể là số liệu của host, không tự quy đổi theo giới hạn cgroup. Giá trị không được hệ điều hành/driver cung cấp sẽ hiển thị không khả dụng; không dùng dữ liệu mẫu. Các số liệu cần hai mẫu để tính tốc độ sẽ có khoảng khởi động ban đầu.

Ứng dụng chạy với quyền người dùng thông thường; thông tin tiến trình/cảm biến có thể bị giới hạn bởi quyền hoặc cấu hình Linux. Không yêu cầu `sudo` để chạy.

## Tuỳ chỉnh không bắt buộc

Biến môi trường `MONITOR_HOST` và `MONITOR_PORT` cho phép đổi địa chỉ/cổng; mặc định vẫn chạy bằng hai lệnh trên. Dashboard cung cấp thông tin máy chủ và tiến trình, dùng trong mạng nội bộ tin cậy. Khi cần truy cập Internet, đặt sau reverse proxy có HTTPS và xác thực.

## Kiểm tra dành cho phát triển

```bash
uv run pytest
```

30 bài kiểm thử bao gồm đọc `/proc` Linux, CPU, tốc độ mạng/I/O và bộ đếm bị reset, GPU không khả dụng, luồng SSE, lịch sử và dọn tác vụ nền. Kiểm tra thực tế trên trình duyệt đã xác nhận cập nhật realtime, chuyển tài nguyên, tooltip, tạm dừng/tiếp tục, desktop/mobile và tự kết nối lại sau khi server khởi động lại. Môi trường phát triển hiện tại là Windows; các nhánh Linux được kiểm tra bằng fixture, chưa chạy trực tiếp trên máy Linux.

Ứng dụng dùng [psutil](https://psutil.readthedocs.io/en/stable/) để đọc tài nguyên, FastAPI/Uvicorn để phục vụ web và JavaScript/SVG cục bộ để vẽ biểu đồ. Mọi asset giao diện đều nằm trong repo, hoạt động không phụ thuộc CDN.
