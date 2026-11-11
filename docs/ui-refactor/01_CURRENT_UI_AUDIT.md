# Báo cáo Kiểm toán Toàn diện Giao diện LabOps Control Center (UI Audit)

**Thời điểm kiểm toán:** Tháng 11/2026  
**Môi trường:** Debian 12 (aiotlab-rack 192.168.167.250), Go Node Agent daemon (port 9100), FastAPI backend (port 8000), Next.js App Router (port 3000).  
**Branch:** `feature/labops-control-center`

---

## 1. Tổng quan Hiện trạng Trước Refactor

Hệ thống điều khiển LabOps Control Center được xây dựng trên nền tảng Next.js 16 (App Router), shadcn/ui và Tailwind CSS v4. Qua quá trình rà soát chi tiết mã nguồn và runtime thực tế, nhóm kỹ thuật đã phát hiện một số khiếm khuyết mang tính hệ thống cần khắc phục triệt để:

### 1.1. Lỗi Sidebar không lọc quyền thực tế (Fake Role-Based Filtering)
* **Vị trí phát hiện:** `src/hooks/use-nav.ts`
* **Triệu chứng:** Hàm `useFilteredNavGroups(groups)` trả về trực tiếp mảng `groups` mà không qua bất kỳ lớp kiểm tra phân quyền nào:
  ```typescript
  // Code cũ:
  export function useFilteredNavGroups(groups: NavGroup[]) {
    return useMemo(() => groups, [groups]);
  }
  ```
* **Hậu quả:** Bất kể người dùng đăng nhập là Nghiên cứu viên (`RESEARCHER`) hay Quản trị viên (`ADMIN`), toàn bộ 11 menu quản trị (Bao gồm Cấu hình máy chủ, Quản lý thành viên, Giám sát tiến trình, Quản lý tài nguyên, Audit logs...) đều hiển thị đầy đủ trên thanh điều hướng.
* **Nguyên nhân gốc rễ:** Thiếu cơ chế kết nối giữa phiên đăng nhập xác thực từ máy chủ (Server Session) và hook hiển thị menu phía client.

### 1.2. Dữ liệu Danh tính Giả lập & Hardcode (Fake Identity)
* **Vị trí phát hiện:** `src/components/layout/app-sidebar.tsx`
* **Triệu chứng:** Sidebar sử dụng đối tượng người dùng cố định:
  ```typescript
  const displayUser = {
    fullName: 'Quản trị viên LabOps',
    imageUrl: '',
    emailAddresses: [{ emailAddress: 'admin@labops.local' }]
  };
  ```
* **Hậu quả:** Một nghiên cứu viên đăng nhập tài khoản cá nhân vẫn nhìn thấy tên "Quản trị viên LabOps" và email `admin@labops.local` ở góc chân trang sidebar, gây nhầm lẫn nghiêm trọng về ngữ cảnh bảo mật và vi phạm nguyên tắc bảo mật thông tin định danh.

### 1.3. Trạng thái Máy chủ Bị Nghịch đảo & Gán Nhãn Cố định (False Host Telemetry)
* **Vị trí phát hiện:** `src/components/layout/header.tsx`
* **Triệu chứng:** Đoạn mã tính toán trạng thái máy chủ trong header:
  ```typescript
  // Đoạn mã cũ:
  allServersOnline ? 'Tất cả máy chủ hoạt động' : 'Workstation Online • Track Mất kết nối'
  ```
* **Thực tế phần cứng:** Máy chủ chính **Track (`aiotlab-rack`)** là máy chủ vật lý đang hoạt động thực tế (`ONLINE`, 3 GPU NVIDIA A30, Go Agent active), trong khi máy trạm **Workstation** đang ở trạng thái chưa xác minh (`UNKNOWN` / `OFFLINE`). Tuy nhiên, nhãn ternary cố định trên lại báo ngược: "Workstation Online • Track Mất kết nối".
* **Hậu quả:** Admin và người dùng nhận thông tin sai lệch về hạ tầng phần cứng.

### 1.4. Phân quyền Tuyến đường Phía Máy chủ Chưa Chặt chẽ (Shallow Route Guards)
* **Vị trí phát hiện:** `src/lib/auth-guard.ts` và các file `src/app/dashboard/*/page.tsx`
* **Triệu chứng:** Các trang chỉ gọi `await protectRoute()`. Hàm `protectRoute()` chỉ kiểm tra xem người dùng đã đăng nhập Clerk hay chưa (`await auth.protect()`), không kiểm tra xem người dùng có quyền `ADMIN` hay `RESEARCHER`.
* **Hậu quả:** Nghiên cứu viên có thể gõ trực tiếp URL `/dashboard/servers`, `/dashboard/users`, `/dashboard/jobs` trên thanh địa chỉ trình duyệt để truy cập vào các màn hình điều khiển quản trị nhạy cảm.

### 1.5. Trùng lặp Chức năng (Duplicate Views)
* Hai tuyến đường `/dashboard/users` và `/dashboard/vpn-ssh` cùng import và render chung component `<MemberManagementView />`. Người dùng nhìn thấy 2 menu khác nhau trên sidebar nhưng giao diện và hành vi hoàn toàn trùng lặp.

---

## 2. Kết luận Đợt Kiểm toán
Cần tiến hành tái cấu trúc toàn diện theo kiến trúc phòng thủ đa lớp (Defense-in-depth):
1. Xây dựng API xác thực phiên máy chủ `/api/labops/session` trả về quyền `role` được ký bảo mật từ backend.
2. Thiết lập cơ chế bảo vệ máy chủ `requireAdmin()` và `requireResearcherOrAdmin()` trên từng trang.
3. Tách biệt hoàn toàn `adminNavGroups` (7 nhóm quản trị) và `researcherNavGroups` (4 nhóm nghiên cứu).
4. Sửa logic header để tính toán trạng thái máy chủ dựa trên dữ liệu thật thu thập từ Go Node Agent.
5. Hợp nhất các màn hình trùng lặp và tổ chức chi tiết thành viên thành 5 tabs rõ ràng.
