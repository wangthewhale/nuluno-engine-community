# Nuluno Engine 單一擁有者雲端試辦：待核准部署

這份配置目前沒有建立 Render 服務、訂閱或 DNS。它啟動已公開且 CI 通過的 Engine source `c54f19d8fa833988e1fba3b5ae265fcc5acbcd16`，不重寫 Engine、MCP 或資料庫。自己的 Mac、GitHub Actions 都不是它的正式 host。這是單一擁有者／單一產品試辦，沒有宣稱可服務多租戶或取得銷售成效。

## 預計費用與本人需要完成的項目

2026-11-16 官方價目：Hobby workspace 基本費 US$0；`0.5c-512mb` compute US$7/月，1GB persistent disk US$0.25/月，因此此配置的基本費約 **US$7.25/月**。Hobby含每月5GB outbound bandwidth，超出目前 US$0.15/GB；稅、流量、建置等可能費用另計。這不是固定總費用保證，也不是效能驗收。若記憶體不足，2GB compute 目前為 US$25/月，須另外核准，不能自動升級。[Render pricing](https://render.com/pricing)

部署前集中確認：

1. 登入使用者擁有的 Render 帳號／workspace，具備建立付費 web service、disk、設定 secrets 及查看帳單的權限；核准上述每月 compute＋disk。現在未核准、未建立。
2. 此 addon 放在公開 community repository 的 `deploy/render-pilot/`；Render選擇的 Blueprint path 是 `deploy/render-pilot/render.yaml`。Dockerfile及build context都明列相對repository root。Render可讀這個公開repo即可，不需要 Nuluno私有methods repo，也不需要改主站。選一個沒有現有服務衝突的名稱。
3. 在 Render 官方 Environment 私下輸入一次隨機 `ENGINE_PILOT_BEARER`（32–256 URL-safe 字元）及真實產品／來源 URL，不傳到聊天或 GitHub。這個 secret 不在 Blueprint 內產生，避免重新同步時換 key。保留同一值供重啟使用。
4. 預設使用 Render 自動提供的 HTTPS `onrender.com` origin；如另外選 `engine-api.nuluno.com`，再授權 Cloudflare 新增該新子網域的 DNS 與驗證 Render TLS，不能覆寫現有 engine/access landing。第一次可不做這步。

不需要 Gmail、社群、廣告、付款平台或模型的新授權。此試辦沒有這些工具，也不自動執行模型。Render 的服務建立按鈕會產生費用，必須等使用者核准。[Secrets setup](https://render.com/docs/configure-environment-variables)

## 部署內容

- `render.yaml`：一個 web service、Singapore、512MB、1GB SSD、單 instance、關閉自動 deploy。發布前要人工審查新 image/source；不是追蹤 GitHub main 自動更新。
- `Dockerfile`：Node24＋Python3.11 image digest固定；下載固定 public commit 的 archive並核 SHA256；官方鎖定 MCP dependencies。只有開機 mount 權限處理使用 root，服務轉 UID/GID10001。
- `start.sh`：確認 `/state` 是實際掛載，再只調整掛載頂層權限。沒有遞迴改檔或每 boot 刪資料。
- `pilot.py`：host-only 開機、備份及撤銷管理，使用原 Store／Service。沒有新增對 agent 開放的 owner API。
- `compose.yaml`：同一份 image 的自架替代方案；只能綁本機反向代理端口，限制512MiB記憶體、0.5CPU及128個PID，需要自己的常駐 Linux host、HTTPS 代理及相同 volume。

Render 的 persistent disk 只能供一個 runtime instance；build、pre-deploy、one-off job 不會掛到它。掛 disk 後沒有零停機 deploy，不能把這份配置直接水平擴展。這些限制決定了此處採單 instance、runtime 初始化與線上 SQLite 備份。[Persistent disks](https://render.com/docs/disks)

## 固定授權範圍

首次設定：

| 變數 | 內容 |
|---|---|
| `ENGINE_PILOT_BEARER` | 私下提供一次、不可提交或每 boot 重新產生 |
| `ENGINE_PRODUCT_URL` | 無帳密、query、fragment 的真實 HTTPS 產品 URL |
| `ENGINE_SOURCE_URLS_JSON` | 1–4個同 origin 的確切公開來源 URL JSON array；空白時只讀產品 URL；允許 `?lang=en`，拒 token/code/session/secret 等 query |
| `ENGINE_RUN_ID` | 預設 `pilot-product`；只有這一個 run |
| `ENGINE_PUBLIC_READ_LIMIT` | 預設12；原 run 的持久累計 quota，不因新 request 或重啟恢復 |

可呼叫：`status`、`contract`、四個 `campaign_*`、`delivery_report`、`execution_metrics`、`cost_summary`、`trust_status`、`learning_status`。外部 client 不能建立另一個 run、換品牌、改 allowlist、執行 generic tool、加模型、發文或花錢。首次 host bootstrap 沿原 Service 建立完整 S01；之後 agent 不開放 `run_create`。

MCP endpoint 是 `<唯一origin>/mcp`，Bearer只放 Authorization header。`/healthz` 是原 gateway 的 process liveness；開機另外核資料庫與設定，它不表示當下所有外部平台或每筆 DB 寫入都健康。Render 會依 custom domain／onrender domain 設 health check 的 Host；只能保留一個 canonical domain，並讓 `ENGINE_PUBLIC_ORIGIN`（如有設定）完全吻合。不要改 gateway Host/origin 驗證去接受任意來源。[Web services](https://render.com/docs/web-services), [health checks](https://render.com/docs/health-checks)

首次預設 origin 取 `RENDER_EXTERNAL_URL`，由 Render 提供。自架 compose 必須私下設定自己的 `ENGINE_PUBLIC_ORIGIN=https://…`，反向代理保留 Host。[Default environment variables](https://render.com/docs/environment-variables)

## 磁碟與重新啟動契約

```text
/state/
  owner.json                   # 原 secret 的hash＋固定scope；不放明文key
  service.json                 # 原worker限制／撤銷狀態
  revocation.json              # 持久deny tombstone；只向撤銷方向收斂
  restore-journal.json          # 一次性restore request紀錄，不從舊backup覆蓋
  workspace/
    initial-profile.json
    workflow.sqlite3
    workflow.sqlite3-wal
    workflow.sqlite3-shm
  backups/                     # 一致性SQLite資料副本與scope/auth設定
```

所有上述狀態都在持久 mount，config/JSON/backup 0600、目錄0700。只允許一個 gateway supervisor 持有該 volume runtime lock。每 boot 核對原 key hash、產品、來源、run、methods及累計 read quota；若變動、資料庫失蹤或檔案可疑，停止啟動。它不換 key、不重建已完成初始化的 run、不補額度。初次開機中斷只允許在同一個已固定 owner intent 下續接。

正常重啟必須保留原 task、receipt、quota及撤銷。若要修改產品／scope，先另做明確 owner 核准與遷移設計，不能修改 env 後靠開機重設。更換來源 code/image也須先備份、審查相容性，再手動 deploy。

## 備份、恢復、撤銷

Root開啟的官方 Shell執行管理指令時，程式也會先轉為原UID10001，避免把config寫成root擁有而使下次開機失效。

程式以24小時為備份排程目標；實際RPO由最近一份成功備份決定，失敗或停機時會更久。SQLite backup API 建立一致性副本，檢查 `quick_check`，轉為獨立 DELETE journal 的備份 DB；不直接複製執行中的 WAL。保存最近3份，內容帶每檔SHA。此排程是備份維護，不是會自行銷售的模型 worker。可在同一服務的官方 Shell 私下執行：

```sh
python /opt/pilot/pilot.py backup
python /opt/pilot/pilot.py revoke
```

撤銷先同步保存 tombstone 再收斂原 config，gateway 每次核對 config；成功完成撤銷後，重啟仍從原 deny tombstone 收斂。`revoke` 不改 task 或 quota。備份目前仍在同一顆 SSD，**不構成異地災難備援**；正式使用前需選擇並核准加密異地備份目的地、保留期限、費用與恢復演練。不要把備份傳到公開 GitHub／R2。

Render disk snapshots不是這裡的 SQLite DB 恢復來源；應用資料恢復用上述 backup。可能損失上次成功備份後的進度；沒有零資料損失、固定RPO或RTO承諾。[Database recovery guidance](https://render.com/docs/disks#restoring-a-custom-database)

需要恢復時由 owner 在 Render Environment 明確提供：

```text
ENGINE_RESTORE_ARCHIVE=backup-<已核對的檔名>.tar.gz
ENGINE_RESTORE_REQUEST_ID=<新的UUID>
```

下一次手動部署會在啟動 gateway之前、持有原 disk lock 時執行。先驗固定白名單、hash、原 owner/scope、SQLite完整性；保留最新 deny tombstone，恢復後一律鎖住舊 worker。原 restore ID完成後，即使 env未移除，後續 boot只讀回原結果，不能反覆倒退資料。owner之後移除這兩個 env；不提供自動「解除撤銷」功能。若要重新授權，必須另行核對遺失進度及原平台狀態後審查，不把舊 key偷偷打開。

## 實際驗收界線

2026-11-16 已實際完成新 Docker image 的 Linux build/boot，並用官方 MCP 客戶端讀真實來源、重建 container、復用原收據、撤銷及還原舊備份後持續拒絕原 key。[原生執行紀錄](https://github.com/wangthewhale/nuluno-engine-community/actions/runs/43846581732)，[逐步結果](../../docs/evidence/render-pilot-linux-20261116.json)。Render 服務尚未建立；這不是 Render TLS、磁碟或計費驗收。授權費用後，仍須在實際 Render 主機核對 HTTPS/MCP、持久磁碟、原 run/receipt、restart、撤銷後401及備份恢復。

這裡也沒有多租戶 isolation、hosted任意產品job provisioning、模型呼叫佇列、完整成本帳單、native conversion ingestion、採用贏家或銷售成效證明。這些不是把一個MCP服務常駐就會自動完成。

## 新增 cloud 費用前的 Linux CI gate

`.github/workflows/render-pilot-check.yml` 只在addon、workflow變更或手動操作時執行，沒有Render API或帳號secret。它實際build此Dockerfile，再在新image執行18個SQLite生命週期與同步順序測試。`tests/container-smoke.mjs` 用一次性隨機CI key、單一named volume及0.5CPU／512MB，走image內官方 `@modelcontextprotocol/client`：

1. 真掛載、非root開機；錯Host、外部Origin仍403。
2. 僅讀自有來源 `https://engine.nuluno.com/?lang=en`；原SQLite來源ledger必須1次／12額度，原回應HTTP200且無轉址。平台阻擋就讓CI失敗，不改來源、換key或拿fixture代替。
3. 刪除container、沿用原volume，重用原source receipt與research；task、原owner fingerprint、quota不變。
4. 真線上SQLite備份；撤銷原key、重建container後原key仍401，沒有新GET或恢復額度。
5. 從較舊、仍active的備份恢復，也必須鎖住原key；相同restore request下次boot不得再恢復或抹掉恢復後marker。

CI傳輸是localhost HTTP搭配固定canonical Host，模擬TLS終止代理後請求；Node `fetch` 會忽略自訂 Host，因此測試透過官方 SDK 支援的 custom fetch，用限定 loopback 的 Node HTTP 傳輸保留指定標頭，另有真 HTTP 回歸檢查。production origin仍只允HTTPS，沒有放寬Host／Origin。它不驗證Render TLS、Render SSD、計費或多使用者負載，不代表服務已上線。第一次 Linux 執行曾抓到還原順序缺陷（先清原 WAL 再 fsync staged DB），第二次則發現上述測試傳輸問題；兩次失敗均保留，沒有替換來源、key或fixture來讓驗收通過。

## 備份的一致性邊界

SQLite backup本身是資料庫一致性副本；host管理命令用同一admin lock固定config、owner、revocation的讀寫，但不是跨SQLite與多個JSON檔的全域ACID transaction。普通MCP資料庫請求仍可能在備份期間繼續，平台外部狀態不在快照內。此pilot只准公開讀，不准私人平台寫入；未來若加入發文、發信或付款，恢復後必須先核對原平台結果，不能只還原DB就重送。

JSON檔先設定0600權限、寫入並對檔案 `fsync`，再以 `replace` 更新名稱並對父目錄 `fsync`。恢復時先完成獨立deny tombstone的同步，再完成pending restore journal的同步；兩者完成後才處理原DB與sidecars。複製的staged DB也先完成檔案 `fsync`，替換後完成workspace目錄 `fsync`，才替換撤銷狀態的config、最後保存complete journal。任一步同步失敗就中止，不能回傳恢復完成。備份archive的rename與刪除也同步父目錄。

新增測試以真實SQLite及受控 `fsync`／替換呼叫故障，核對上述順序、pending保留、原request重試和撤銷收斂。**這不是斷電測試**，不代表任意儲存硬體、檔案系統或忽略 `fsync` 的環境都不會遺失資料，也沒有「任何時刻crash都已完成撤銷」的宣稱。

restore journal與最新deny tombstone不從舊archive覆蓋；任何恢復都撤銷原worker，避免舊備份復活權限。這仍仰賴可信host／Render owner，檔案hash校完整性，不是簽章，也不防已掌控host的人。自有磁碟上3份備份不是異地災難備援。沒有模型worker、任意產品多租戶provisioning或thousands使用者驗收，沒有保證成交或持續自我改進的宣稱。
