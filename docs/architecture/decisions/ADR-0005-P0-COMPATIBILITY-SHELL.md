# ADR-0005 P0 使用 World Monitor 兼容壳完成纵向切片

状态：已接受

日期：2026-08-26

## 背景

ADR-0004 选择 World Monitor 作为 P0 展示方向，同时规定了退出条件：如果完整上游依赖阻碍 Event、Claim、Evidence 闭环，应保留交互思想并切换到精简展示端。

P0 执行环境可以通过 GitHub 连接读取上游文件，但无法稳定下载完整源码树，也无法创建 GitHub Fork。World Monitor 当前生产仓库还包含大量 P0 不需要的云端、桌面、计费和领域接口。

## 决定

P0 使用独立实现的 World Monitor 兼容壳完成可运行版本。它保留地图优先、三栏面板、事件流、时间线、来源健康、故障可见和情报缺口等交互原则。

PLDR 的 FastAPI、SQLite 和领域对象保持独立，统一通过 `/pldr-api/v1/*` 提供数据。P1 可以把同一套 API 接入完整 World Monitor 上游 Panel，也可以继续演进当前自有前端。

## 边界

当前 `apps/dashboard/` 不声称是完整 World Monitor 源码的修改版，也不包含上游源码树。界面代码是为 PLDR P0 单独实现的兼容壳。

任何正式复制、修改或部署上游代码，都必须单独保留 AGPL-3.0-only 许可证、版权和 commit 记录。

## 结果

该选择在 P0 中降低了外围依赖，同时保留未来迁移路径。已完成的证据后端、对象模型、API、自动测试和演示数据均可继续进入 P1。
