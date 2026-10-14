# Photo Dream

FastAPI + PostgreSQL 分镜生图项目，保留现有静态前端和 multipart API。Python 3.10+。

## 重构方案与实现

- `web/app.py`：应用工厂、生命周期、静态文件及 Uvicorn 入口。
- `web/routers/`：项目、卡片、剧本、图片生成/编辑及健康检查接口。
- `web/services/`：提示词组合、参考图拼接、图片中转调用、镜头历史。
- `web/repository.py`、`web/models.py`、`web/database.py`：SQLAlchemy + psycopg 数据访问。
- PostgreSQL `projects` 表使用 JSONB 保存项目聚合（卡片、分镜计划、镜头及参考图元数据），保留 API 的原有数据结构。项目修改在事务中使用行锁，防止并发追加互相覆盖；调用图片接口期间不持有行锁。
- 上传和生成的图片仍保存在 `web/runtime/uploads`、`web/runtime/outputs`，数据库保存其 URL。备份时需要同时备份 PostgreSQL 和图片目录。
- 同步图片调用运行在 FastAPI 工作线程中。配置接口只返回 `has_api_key`，不再返回服务端 API Key。
- 首次启动自动创建初始表。后续结构变更应使用版本化数据库迁移。

## PostgreSQL 的 docker run 命令

宿主机数据固定挂载到 `/opt/database/postgre`；使用 PostgreSQL 17 对应的容器数据路径：

```bash
sudo mkdir -p /opt/database/postgre

docker run -d \
  --name gpt_workflow_postgres \
  --restart unless-stopped \
  -p 127.0.0.1:5432:5432 \
  -e POSTGRES_DB=workflow \
  -e POSTGRES_USER=workflow \
  -e POSTGRES_PASSWORD=change_me_to_a_strong_password \
  --health-cmd="pg_isready -U workflow -d workflow" \
  --health-interval=5s --health-timeout=5s --health-retries=12 \
  -v /opt/database/postgre:/var/lib/postgresql/data \
  postgres:17
```

将示例密码换成自己的强密码。下面的 `DATABASE_URL` 使用相同密码。直接拼接连接 URL 时使用 URL 安全的密码；特殊字符需要 URL 编码。已有数据库目录的账号密码不会随环境变量重新初始化。

## 本地启动 FastAPI

从项目根目录执行：

```bash
python -m venv venv
venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 中的 DATABASE_URL（连接上面的 PostgreSQL）和可选 HICODE_API_KEY。
venv/bin/python -m uvicorn web.app:app --host 0.0.0.0 --port 8090 --reload --env-file .env
```

Windows：将 `.env.example` 复制为 `.env` 并配置后，执行 `start.bat`；或者：

```bat
venv\Scripts\python.exe -m uvicorn web.app:app --host 0.0.0.0 --port 8090 --reload --env-file .env
```

页面：<http://127.0.0.1:8090/photodream/>（未登录自动进入登录页）；API 文档：<http://127.0.0.1:8090/photodream/docs>（需登录）；数据库健康检查：`GET /photodream/api/health`。

## 创作中台、登录和团队

整个应用挂载到 `/photodream`，例如 `/photodream/login`、`/photodream/single`、`/photodream/storyboard`、`/photodream/api/health`、`/photodream/static/`。历史数据库中的图片路径保持 `/uploads/...`、`/outputs/...`，前端显示和下载时自动补上 `/photodream`，无需迁移图片记录。登录 Cookie 也限定在 `/photodream` 路径下。

首页 `/photodream/` 为统一管理中台，包含总览、项目管理、资源中心、团队空间、账号设置，以及管理员的用户管理。统一菜单还提供单图工作台 `/photodream/single` 和分镜工作台 `/photodream/storyboard`；项目卡片可直接打开对应项目的两个工作台。

中台与两个工作台共用固定导航、侧栏和页面内路由。菜单切换无需整页刷新，支持浏览器前进/后退和直接访问工作台地址；首次打开按需加载，后续切换复用已挂载的编辑器，保留当前项目、提示词、已选文件及滚动位置。配置请求共享缓存，返回工作台时后台同步项目资源。主动切换项目会恢复该项目的草稿，并清空临时文件选择；刷新整个页面后仍需重新选择本地文件。

首次部署或从旧版本升级后，在项目目录初始化管理员：

```bash
venv/bin/python -m web.manage admin --claim-legacy
```

密码由终端交互输入两次，至少 10 位，不存在默认密码。命令加载 `.env`，创建管理员并将尚无归属的历史项目分配给此账号；已有归属的项目不受影响，原始项目、分镜、卡片、图片和操作记录保留。不加 `--claim-legacy` 则只创建账号。普通网页注册不会取得历史项目或管理员权限。Windows 使用 `venv\Scripts\python.exe -m web.manage admin --claim-legacy`。容器部署可执行 `docker exec -it gpt_workflow python -m web.manage admin --claim-legacy`。

用户默认只访问自己的个人项目和已加入团队的项目；系统管理员也不会自动获得其他用户的私人项目权限。后端对项目读写、分镜、生成、卡片、事件记录、诊断接口以及 `/photodream/uploads`、`/photodream/outputs` 图片文件统一检查权限。密码以加盐 scrypt 哈希存储；登录使用数据库中可撤销的会话和 HttpOnly / SameSite Cookie，持续 7 天。修改密码、管理员重置密码或停用账号会使会话失效。登录和注册按来源地址限制尝试次数。

项目管理支持创建、搜索、空间和归档筛选、修改名称/简介、归档/恢复、设置个人或团队共享。归档保留数据；团队成员可共同编辑项目，管理员/所有者可归档，只有团队所有者可把项目移出原团队。项目变更记录包含操作者 ID。浏览器草稿按用户 ID 分开存储。

团队支持创建、改名、邀请加入、管理员/成员角色调整、移除成员和主动退出。邀请为一次性邀请码，48 小时过期，可撤销；所有者不能被普通角色调整或移除。资源共享以团队项目为单位，成员可访问其中的分镜、角色卡、场景卡、参考图与生成结果；资源中心汇总可访问卡片，并可导入副本到其他有权限的项目。离开/被移出团队后，即使原来创建过团队项目，也不能继续访问该团队数据；已授权复制到其他项目的资源副本保留。

管理员可以创建普通账号、查看用户、启用/停用账号、重置其他账号的密码；用户可以修改显示名称和自己的密码。关闭公开注册时仍可由管理员创建账号。在 `.env` 设置 `ALLOW_REGISTRATION=false`；HTTPS 部署设置 `COOKIE_SECURE=true`，本地 HTTP 使用 `false`。前端和 API 使用同一站点；带 Cookie 的跨站写入会被拒绝。自定义图片接口需要填写自己的 API Key，服务端 Key 只能发送到服务端配置的接口地址。

团队协作按操作保存，修改同一字段采用最后一次成功保存的值；目前没有实时协同光标或冲突合并。页面文件和脚本本身不含业务数据，业务 API 与图片必须登录访问。旧版未同步的浏览器草稿仍留在原本的 localStorage 键中，不会自动归入新注册用户。

`DATABASE_URL` 必填，只支持 PostgreSQL；不可连接时应用启动失败。`.env` 由 Uvicorn 的 `--env-file` 加载。`python -m web.app` 也可启动（默认端口 8000），但需要提前设置环境变量。

## 两个容器的 docker run 部署

如果已按上面的命令启动数据库，先把它接入应用网络：

```bash
docker network create workflow-net
docker network connect workflow-net gpt_workflow_postgres

docker build -t gpt-workflow:latest .
mkdir -p web/runtime
# 应用容器使用 UID 10001；迁移旧数据时也要确保其文件可写。
sudo chown -R 10001:10001 web/runtime

docker run -d \
  --name gpt_workflow \
  --restart unless-stopped \
  --network workflow-net \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql+psycopg://workflow:change_me_to_a_strong_password@gpt_workflow_postgres:5432/workflow \
  -e HICODE_API_KEY=your_image_api_key \
  -v "$(pwd)/web/runtime:/app/web/runtime" \
  gpt-workflow:latest
```

可省略 `HICODE_API_KEY`，在页面填写请求所用的 Key。页面地址：<http://127.0.0.1:8000/photodream/>。应用图片目录和 PostgreSQL 数据目录分别挂载，数据库文件不会写入应用容器。首次启动前可用 `docker exec gpt_workflow_postgres pg_isready -U workflow -d workflow` 检查数据库是否就绪。

## Docker Compose 部署

### 同域名 Nginx / PM2 部署

已有站点可以把本系统放在 `https://www.ploverjoe.xyz/photodream/`。应用直接运行在宿主机时，数据库连接使用 `127.0.0.1:5432`；使用 Python 3.12 的虚拟环境 `venv312`，由 PM2 守护：

```bash
pm2 start /opt/project/GPT_workflow/venv312/bin/python \
  --name photo-dream --interpreter none \
  --cwd /opt/project/GPT_workflow --kill-timeout 330000 \
  -- -m uvicorn web.app:app --host 127.0.0.1 --port 3033 \
  --env-file /opt/project/GPT_workflow/.env \
  --proxy-headers --forwarded-allow-ips 127.0.0.1
pm2 save
```

执行 `pm2 startup` 并按输出配置开机启动。将 [Nginx location 配置](deploy/nginx-photodream.conf) 加入已有 HTTPS `server` 块内部，保留原来其他服务的 location。`proxy_pass http://127.0.0.1:3033;` 后面不要加 `/`，Nginx 必须保留 `/photodream` 前缀；也不需要额外配置 Uvicorn `--root-path`。HTTPS 下设置 `.env` 的 `COOKIE_SECURE=true`，应用读取 `X-Forwarded-Proto` 以正确进行同源检查。

```bash
sudo nginx -t
sudo systemctl reload nginx
curl http://127.0.0.1:3033/photodream/api/health
```

代码更新后，等待生成请求完成再 `pm2 restart photo-dream`。浏览器首次升级刷新并重新登录；旧 Cookie 的根路径不再使用。

### 新建数据库的 Compose 部署

也可以在尚未用 docker run 启动同名容器时使用 Compose：

```bash
cp .env.example .env
# 修改 POSTGRES_PASSWORD 和可选 HICODE_API_KEY。
mkdir -p web/runtime
sudo chown -R 10001:10001 web/runtime
sudo mkdir -p /opt/database/postgre
docker compose up -d --build
```

Compose 使用服务名 `postgres` 连接数据库，不使用 `.env` 中的本地 `DATABASE_URL`。数据库不向宿主机暴露端口。通过健康检查后才启动应用。

## 胶片分镜工作台

访问 `/photodream/storyboard`（旧页面顶部也有入口）。没有项目时自动创建默认项目；首次打开没有镜头的项目，初始化一个空白镜头。已有项目保留原来的镜头数量和内容。之后由用户点击胶片区域末尾的「＋ 新建分镜」添加镜头，无须输入或确认。胶片与大图预览合并为横向画廊，中央镜头放大、两侧镜头缩小并部分露出；支持横向滚动、触控、左右方向键和点击选中。

点击胶片左上角的「×」或编辑区的「删除这个镜头」，确认后将镜头及其版本列表移出工作台，独立生成日志与原图保留，其他镜头与参考卡不受影响。删除当前镜头后选中相邻镜头；正在生成的镜头暂不可删除。允许删除最后一个镜头，刷新或重新打开项目不会自动补回，之后由用户手动添加。删除镜头保留图片文件，避免影响其他引用。

每个镜头独立保存名称、提示词、模型、尺寸、星标和选中的角色/场景卡。点击胶片上的星标标记需要处理的镜头，再点击「生成 / 更新已标记」，一次发起这些镜头的异步请求。已标记的已完成镜头也会重新生成，未标记镜头保持原结果；生成后保留标记，可取消。标记会保存到 PostgreSQL，刷新后仍保留。如果已标记镜头缺少提示词，先提示补全，再开始本次批量生成。单个镜头仍可独立重新生成。

镜头位置不会随请求完成顺序改变；重新生成期间保留旧图，成功后保存最近 20 个历史版本，失败可独立重试。页面每 2.5 秒同步状态，刷新后可恢复提示词和结果。同一镜头生成期间拒绝重复请求及编辑，服务中断后过期状态允许重试。

生成请求在 FastAPI 工作线程执行，页面一次提交已标记镜头的请求。已经提交的请求通常在关闭页面后继续在服务端执行，可重新打开页面查看状态。生成期间不持有数据库锁；API Key 只用于请求，不保存在镜头记录中。

新增接口：`GET/POST /photodream/api/projects/{project_id}/frames`、`POST /photodream/api/projects/{project_id}/frames/initialize` 幂等初始化、`POST /photodream/api/projects/{project_id}/frames/{frame_id}` 保存镜头、`DELETE /photodream/api/projects/{project_id}/frames/{frame_id}` 删除镜头、`POST /photodream/api/projects/{project_id}/frames/{frame_id}/generate` 生成镜头。镜头记录保存在项目 JSONB 的 `frames` 字段，`frames_initialized` 标记防止删除后自动补回，无须新建数据表。

分镜工作台左侧可直接创建角色卡 / 场景卡，填写设定并上传参考图。参考卡库支持多选、全选、清空；点击「应用到镜头」后多选目标镜头或全选，将这些镜头的卡片选择统一替换为所选参考卡。每个镜头的编辑区也可以独立多选、全选或清空参考卡，选择自动保存。生成时将卡片设定合入提示词，带图片的卡片会拼成参考图交给图片编辑接口，并要求保持人物外貌和服装一致。批量应用接口为 `POST /photodream/api/projects/{project_id}/frames/apply-cards`，在单个事务内验证卡片和目标镜头后保存；生成中的镜头暂不可修改。

可选浏览器验证（需要 `/usr/bin/google-chrome` 和 `pip install playwright`）：`TEST_DATABASE_URL=postgresql+psycopg://用户:密码@127.0.0.1:5432/测试库 venv/bin/python -m pytest tests/test_console_browser.py -q`。自动启动隔离的本地服务，以模拟图片接口验证标记后统一生成/更新、失败重试、单镜头重新生成、自动保存、胶片滑动，以及真实注册、项目管理、团队共享和资源导入流程，不消耗 API 额度。测试只在临时 schema 内创建数据。

两个页面的模型支持候选建议及手动输入；分辨率提供常用「比例 · 宽 × 高」选项，并支持符合格式的自定义尺寸和实时比例显示。预设包含 1:1、16:9、9:16、4:3、3:4、3:2、2:3、4:5、5:4、21:9、9:21、2:1、1:2、16:10、10:16，多种尺寸最高包含 3840×2160；模型和尺寸能否生成以实际图片接口支持为准。

提示词分为「系统提示词」（可选：画风、规则、一致性要求）和「用户提示词」（当前画面内容）。镜头和历史版本分别保存两部分，旧 `prompt` 字段继续表示用户提示词，新增 `system_prompt` 表示系统要求。当前图片接口只接收一个 `prompt`，后端按系统要求、参考卡设定、用户描述组合发送，不使用聊天接口的 system/user 消息角色。

## 自动保存与操作记录

项目、镜头、系统/用户提示词、模型、尺寸、星标、参考卡选择、生成结果和状态均保存到 PostgreSQL。分镜输入停止约 400 毫秒后自动保存；未填完整的模型和尺寸也可保存为草稿，生成时再校验。两个页面的未提交文字草稿、当前分镜、参考卡库选择、分镜接口地址和未提交的卡片描述也会保存到项目的 `workspace` 中。切换镜头、切换项目或页面进入后台时尝试立即同步。

输入时先写入浏览器本地草稿，再请求数据库。保存失败保留本地副本并自动重试；刷新后恢复草稿并补传。只有收到数据库成功响应才清除对应草稿。未创建项目的单图草稿暂存在本机，创建项目后转存数据库。API Key 不写入浏览器缓存、项目或操作记录；尚未提交上传的文件不会恢复，需要重新选择。

`project_events` 表记录本次功能启用后的创建、修改、删除、生成和状态变更，保存修改前后的内容，与项目更新在同一个事务中提交或回滚；重复保存且内容不变时不新增记录。删除的镜头完整内容以及历史版本的变化仍可在记录中追溯。不会补录过去的操作。侧栏「操作记录」展示最近记录；完整内容通过 `GET /photodream/api/projects/{project_id}/events?limit=50` 查看，使用返回的最后一个 `id` 作为 `before` 参数向前翻页。工作区保存接口为 `POST /photodream/api/projects/{project_id}/workspace`。

数据库落盘和本地草稿不能替代备份。数据库和图片目录需要一起备份，并将备份复制到其他磁盘或服务器；本地草稿只存在于当前浏览器，离线时必须恢复同步后才能在另一台设备看到。不要清理浏览器数据或删除 `web/runtime` 来排查保存问题。

## 图片下载、资源删除与生成日志

中台菜单「生成日志」汇总所有有权访问项目的生成记录，可按项目、镜头 ID、状态筛选并加载更早记录。项目卡片和分镜工作台也提供日志入口。详情展示系统提示词、用户提示词、实际发送的完整提示词、模型、尺寸、操作人、生成时间、原始图片路径和当时的角色/场景卡快照；可复制完整 JSON 记录。失败请求记录错误，中断超时的请求显示为「已中断」。日志沿用个人/团队权限，管理员不会自动获得他人私人日志权限。

新增 PostgreSQL 表 `image_generations`，请求发送前保存快照，成功后先提交图片记录再更新工作台。分镜界面只展示最近 20 个版本，单图列表受 `HICODE_MAX_PROJECT_SHOTS` 限制，独立生成日志不受这些上限影响，不会因重新生成、删除镜头或删除参考卡而删除。图片文件使用独立名称保存在 outputs 中。API Key 不记录到快照，错误中的请求密钥会被隐藏。

角色卡和场景卡支持「删除卡片」「移除图片」「下载参考图」，单图结果、分镜图片、历史版本和日志都提供下载。删除卡片同步取消镜头/草稿关联；移除图片保留卡片文字。使用该卡片的镜头正在生成时暂不能删除。删除为移出当前资源库，不物理删除历史文件，操作前后的数据仍在 `project_events` 中。

分镜可移除当前图片，单图历史可移除图片记录；这两种操作同样保留原图及生成日志。删除镜头也不会删除其生成档案。

首次升级启动自动创建表，并从现有项目和操作记录补录可识别的旧生成图片。历史缺失的提示词、操作人、参考快照不会凭空补齐，详情显示为历史补录。磁盘文件已丢失或没有任何项目/事件引用的文件无法自动恢复归属；同时保留数据库与 `web/runtime/uploads`、`web/runtime/outputs` 才能下载历史图片。

API：`GET /photodream/api/generations?project_id=项目ID&limit=30`，返回 `items` 和 `next_before`；下一页使用 `before=next_before`。详情为 `GET /photodream/api/generations/{id}`；已授权图片路径添加 `?download=true` 返回附件。删除卡片为 `DELETE /photodream/api/projects/{id}/cards/{character|scene}/{card_id}`，仅移除图片在该路径后加 `/image`。

## 旧项目迁移

启动时检测当前 runtime 下的 `projects.json`，在一个事务中导入 PostgreSQL，保留原文件。已有同 ID 的数据库项目不会被覆盖；迁移记录确保成功后不会重复导入。损坏的 JSON 会导致启动失败并回滚，修复文件后可以重启。

原 `web/runtime` 目录需要完整保留：迁移数据的图片 URL 仍指向原 `uploads`、`outputs`。不要将应用图片目录放入 PostgreSQL 的数据目录。

## 配置与验证

参考 `.env.example`。可使用 `WORKFLOW_RUNTIME_DIR` 改变图片/旧数据目录；`CORS_ORIGINS` 用逗号分隔独立前端来源，默认前后端同源。`HICODE_REQUEST_TIMEOUT`、`HICODE_MAX_PROJECT_SHOTS` 分别控制上游请求超时和保留镜头数量。

```bash
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
# 集成测试创建独立随机 schema，不使用业务项目数据：
TEST_DATABASE_URL=postgresql+psycopg://workflow:your_password@127.0.0.1:5432/workflow venv/bin/python -m pytest -q
venv/bin/python -m ruff check web tests
```

未设置 `TEST_DATABASE_URL` 时，PostgreSQL 集成测试会跳过。集成测试覆盖迁移、事务回滚、并发卡片/镜头追加、生成/编辑/连续分镜及上传；图片中转调用使用模拟响应，不消耗生图额度。
