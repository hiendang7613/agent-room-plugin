# Agent Room

Một đội Claude Code + Codex làm việc cùng bạn trong project. **Bạn diễn đạt mục tiêu; đội agent
quản lý phối hợp và báo lại kết quả.** Members dùng harness native và được khuyến khích hỏi nhau,
brainstorm, phản biện, nhắc việc, chia sẻ kinh nghiệm và chủ động đề xuất trong phạm vi đã giao.

**Plugin 0.3.20 · schema 3.** [ZIP cài đặt](dist/agent-room-0.3.20.zip) ·
[Kiểm chứng](docs/verification-0.3.20.json) · [Kế hoạch benchmark](docs/benchmark-v2-plan.md).
Bản ZIP này cải thiện khôi phục tin gửi lỗi và hướng dẫn khi tiếp tục phiên. Source đang có thay đổi
chưa đóng gói sau 0.3.20; ZIP 0.3.20 chưa có room bốn người mặc định hoặc notify-all.

## Báo cáo dễ đọc với i-have-asd-ste100

Agent Room cài kèm [i-have-asd-ste100](https://github.com/hiendang7613/i-have-asd-ste100): mọi báo cáo gửi bạn có cùng một hình dạng,
bằng ngôn ngữ của bạn. Các dòng mở đầu bằng từ khóa, rồi một câu **Conclusion**, rồi đủ sáu mục:
0. Done, 1. InProgress, 2. Questions, 3. Todos, 4. Pending, 5. Backlog. Phương án khuyến nghị ghi `<a>`.
Tắt trong một phiên bằng "stop ste mode".

*Readable reports: Agent Room installs i-have-asd-ste100, so every report from the room ends with the same short,
predictable conclusion and six fixed sections, in your language.*

## Bắt đầu làm việc

Trong Claude Code, mở project muốn làm việc rồi dùng:

```text
/init-agents-space
```

Mỗi room mới có đủ bốn members: **CLAUDE_01, CODEX_01, CLAUDE_EXPERT, CODEX_EXPERT**. CLAUDE_01
là gateway bạn đang chat; ba member còn lại nhận thông báo room. `--mode full` vẫn chạy như alias
tương thích, cùng roster bốn người.

Mọi tin room xếp hàng cho ba member còn lại; supervisor thử gửi ngay khi queue hoạt động. Bản sao
broadcast là FYI; chỉ member được gọi đích danh sở hữu yêu cầu hoặc task. Tin vẫn chờ nếu room/member
đang dừng hoặc bị pause. `agent-room wakes` phân biệt hàng đợi, lần thử gửi và ACK; các số đó chưa
chứng minh agent đã đọc hoặc cho biết token/cost.

Roster đặt Sonnet 5.5 / Luna 6 / Opus 5.5 / Sol 6.1, tất cả xhigh. Thành viên được khởi chạy nhận
model/effort qua native launch, Codex thread start/resume và mỗi lượt Codex mới; một turn đang chạy
và session Claude cũ được resume không bị đổi giữa chừng. CLAUDE_01 là session host bạn đang dùng nên room không ép model hay
effort vào session đó. `agent-room status` tách requested settings khỏi model host báo lại; nếu host
không báo model, trường observed để trống. Cài đặt này chưa được chạy với provider thật và không phải
bằng chứng về model được chọn, token hay cost.

Claude Code dùng alias `sonnet`/`opus`; [tài liệu Anthropic](https://docs.anthropic.com/en/docs/claude-code/model-config)
nói alias trỏ tới model mới nhất theo provider và có thể đổi theo thời gian. Tại lần kiểm tra 2026-10-02,
tài liệu ghi Anthropic API ánh xạ chúng tới Sonnet 5.5/Opus 5.5, nhưng room không kiểm tra provider
hoặc cấu hình tài khoản. [Tài liệu Codex](https://developers.openai.com/codex/models) công bố ID
`gpt-6-luna`/`gpt-6.1-sol`; quyền truy cập tùy plan, client và rollout. `xhigh` là effort được yêu cầu,
còn model/host phải hỗ trợ mức đó. Các request không bảo đảm model đã chạy với phiên bản hay effort mong muốn.

CLAUDE_01 là session bạn đang chat; bạn tiếp tục giao việc ở đó bằng ngôn ngữ tự nhiên. Ví dụ:

> Tìm nguyên nhân lỗi upload, sửa trong scope hiện tại, nhờ đồng đội kiểm tra rồi báo kết quả.

> Hai bạn xem có cách nào đơn giản hơn không, trao đổi và đề xuất phương án.

> Tiếp tục công việc đã giao. Nếu có bài học đáng giữ, các bạn tự ghi lại và chia sẻ.

Main tự xử lý task, scope, claim, inbox, review và knowledge qua công cụ của room. Các members
trao đổi trực tiếp khi hữu ích. Admin không cần tạo JSON, tra record ID hoặc quản lý từng tin nhắn.
Đây là hướng dẫn cho agent, không phải bộ máy cưỡng chế tự hoàn thành mọi việc.

Vai trò không giới hạn đóng góp hay thảo luận.

## Theo dõi và tiếp tục

Bạn có thể hỏi “tiến độ thế nào?”, “chi tiết phần review” hoặc “tiếp tục” ngay trong cuộc trò chuyện.
Main báo kết quả, tiến độ có ý nghĩa và điều cần bạn quyết định; chi tiết kỹ thuật được giữ để tra cứu.
Một số lệnh điều khiển khi cần:

```text
/agent-room:status
/agent-room:stop
/agent-room:start
/agent-room:doctor
```

`status` và `doctor` chỉ kiểm tra. `stop` giữ công việc chưa xong; `start` tiếp tục đúng native sessions.
Khi main đóng, workers dừng; khi mở lại, room có thể resume. Stop thủ công được giữ tới lần start.
Nếu native host yêu cầu thao tác của bạn, main giải thích lý do và bước nhỏ nhất cần thực hiện.

Đội agent chủ động trong quyền hiện có. Thay phạm vi đáng kể, chi phí provider, credentials,
commit/push/publish/deploy vẫn cần quyền tương ứng. Ý tưởng của peer hoặc knowledge không cấp quyền.
Một lần gửi tin thành công chưa chứng minh peer đã xử lý; review và task completion cần bằng chứng.

## Cài plugin local

Yêu cầu **macOS, Python 3.11+, Claude Code có background sessions và Codex có app-server**.
Đăng nhập qua CLI native của từng sản phẩm trước. Plugin giữ quyền và thông tin đăng nhập native.

Giải nén [ZIP 0.3.20](dist/agent-room-0.3.20.zip) vào thư mục ổn định, rồi trong Claude Code:

```text
/plugin marketplace add /absolute/path/to/install/agent-room
/plugin install agent-room@agent-room-marketplace
```

Mở lại Claude Code trong project. Plugin đăng ký `/init-agents-space`; nếu tên đó đã thuộc skill khác,
file cũ được giữ và bạn dùng `/agent-room:init-agents-space`. Init tạo `agents_space`, thêm block được
quản lý vào `AGENTS.md`, `CLAUDE.md`, `.gitignore` và giữ nội dung khác. Room thủ công đã tồn tại cần
migration riêng; chạy lại init không đổi mode hay tạo thêm team. Repo chưa tự được publish lên GitHub.

Room đang chạy cần stop/start để nạp code mới; việc đổi mode không còn cần thiết cho roster bốn người.
Main/operator xử lý chi tiết; xem [upgrade và recovery](docs/v1.1.md) khi có xung đột hoặc nâng phiên bản.
Re-init giữ guide tùy chỉnh. Agent có thể đọc tham chiếu hiện tại bằng `agent-room guide collaboration`.
Controller/context native đang chạy không tự cập nhật chỉ vì source/ZIP mới đã được tạo.

## Tìm hiểu thêm khi cần

- [Cách đội agent phối hợp và giao tiếp với admin](templates/conventions/collaboration.md).
- [Học từ trải nghiệm, giới hạn áp dụng và phản chứng](templates/conventions/learning.md).
- [Vận hành, quyền native, kiểm thử và các pilot trước](docs/operations.md).
- [CLI, schema và nâng phiên bản](docs/v1.1.md).
- [Lịch sử kiểm chứng và giới hạn số đo](docs/verification.md).
- [Các repo liên quan](related_repos.md) và [nghiên cứu source tham khảo](ref_repos/README.md).

Runtime **2.764 LOC**, không thêm module, scheduler, framework hoặc model loop ở 0.3.20.
Bản ghi kiểm chứng phân biệt test dùng harness giả, review source và giao tiếp native.
Chưa có benchmark model thật chứng minh Agent Room tiết kiệm token, nhanh hơn hoặc là lựa chọn tốt
nhất so với mọi repo. [Protocol benchmark](docs/benchmark-plan.md) định nghĩa cách kiểm tra điều đó
trên cùng workload, giữ cả thất bại và số lần cần admin can thiệp.
