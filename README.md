# japan-antiwar-intel

日本反戰運動公開情報系統。本系統僅處理公開來源資訊，嚴格遵循以下邊界：

- **不登入**：不登入任何需帳號密碼的私人平台或會員區域。
- **不繞過驗證碼**：不破解、不繞過 CAPTCHA 或任何自動化防護機制。
- **不抓取私密內容**：不爬取私人訊息、非公開社團、會員限定內容或付費牆後內容。
- **不做普通個人成員畫像**：不對不具公眾人物身分的普通個體建立畫像或追蹤其行為。

分析必須保持中立，區分事實、來源觀點和模型推斷；證據不足時寫「不足以判斷」。

## 一鍵啟動

當前機器已配置為使用 WSL2 內建 Docker Engine，不依賴 Docker Desktop。

最簡單方式：

1. 雙擊桌面捷徑「日本反戰情報系統 - 一鍵啟動」。
2. 啟動器會自動啟動後台服務並打開瀏覽器。
3. 通常地址是 `http://127.0.0.1:8190`；如果 Windows localhost 轉發不穩定，啟動器會自動改用 WSL IP。
4. 保持啟動器視窗開啟，它負責保持 WSL 不休眠。

專案根目錄也有同一個入口：`啟動日本反戰情報系統.vbs`。

## 服務

- n8n: `http://localhost:5782`
- FreshRSS: `http://localhost:8181`
- RSSHub: `http://localhost:1210`
- changedetection.io: `http://localhost:5100`
- Qdrant: `http://localhost:6433`
- analysis-worker / 圖形控制台: `http://localhost:8190`

## 圖形控制台功能

打開 `http://localhost:8190` 後可以直接在瀏覽器裡：

- 查看 PostgreSQL 和 Qdrant 狀態。
- 手動提交公開文章。
- 抓取 GDELT 公開新聞索引並入庫。
- 選擇日期生成週報。
- 查看 `reports/` 中的 Markdown 報告。

## .env 設定

`.env.example` 提供變數模板。複製為 `.env` 後填寫必要值：

- PostgreSQL 使用者、密碼、資料庫名稱。
- n8n 加密金鑰。
- DeepSeek API key（可選，無 key 時自動生成 fallback 報告）。
- 容器訪問公網所需的代理橋接位址（可選）。

**不要將 `.env` 提交到 Git。** `.env.example` 只保留變數模板，不含任何金鑰或密碼。

## 架構

- **n8n**：自動化排程器。
- **PostgreSQL**：n8n 和 analysis-worker 共用資料庫。
- **FreshRSS**：RSS 新聞池，匯入日本反戰相關公開來源。
- **RSSHub**：補充沒有標準 RSS 的公開來源。
- **Redis**：RSSHub 快取。
- **changedetection.io**：監控官方網站、和平團體網站頁面變化。
- **Qdrant**：向量庫。有 embedding API 時寫入向量並優先用於聚類。
- **analysis-worker**：FastAPI 服務，執行清洗、去重、實體識別、主題分類、聚類、歷史對比、GDELT 接入、週報生成。

## 公開來源範圍

系統從以下類型的**公開**來源收集資訊：

- 新聞媒體公開 RSS/Atom feeds
- Google News 公開搜索結果
- Reddit 公開 subreddit 的 JSON API 和 old.reddit.com 頁面
- GDELT Project 公開新聞索引
- 官方網站公開頁面（透過 changedetection.io 監控變更）

**明確排除**：
- 需登入的社交平台（Twitter/X 私人帳號、Facebook 非公開社團等）
- 需付費或會員驗證的媒體內容
- 任何形式的 CAPTCHA 繞過或自動登入
- 對非公眾人物的個人資料收集或行為追蹤

## 已實現

- Docker Compose 啟動所有服務。
- 圖形化啟動器和瀏覽器控制台。
- `/health`、`/ingest`、`/gdelt/fetch`、`/analyze/weekly`。
- HTML 清洗、URL/hash 去重。
- 規則實體識別和主題分類。
- GDELT 查詢：支援日期範圍，查詢日本反戰、和平運動相關關鍵詞。
- 優先 Qdrant embedding 聚類；無 embedding 時回退本地相似度。
- 過去 4 週歷史對比。
- DeepSeek/OpenAI-compatible Chat Completions 調用。
- 大模型失敗或未配置時自動生成 fallback Markdown 週報。
- 週報包含事件簇表、主題升溫/降溫表、實體頻次表、來源覆蓋情況和證據等級說明。

## 簡化版或預留

- 實體識別是詞典匹配，不是完整 NLP。
- 主題分類是關鍵詞規則，不是監督模型。
- 多來源交叉驗證第一版按來源類型歸組；細粒度媒體立場差異仍需大模型或人工複核。
- Qdrant 聚類依賴已配置 embedding API；沒有 embedding key 時不會報錯。
- n8n 工作流需要在 n8n 界面中搭建，說明見 `n8n-workflows/README.md`。

## GDELT 驗證

在圖形控制台的 GDELT 區域填寫日期，點選「抓取並入庫」。GDELT 失敗不會影響主流程；會返回空列表或跳過失敗查詢。

## Qdrant 驗證

打開 `http://localhost:6433/collections/japan_antiwar_articles`。只有配置 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL` 並成功入庫文章後，這個集合才會有資料。

## 備份

資料持久化在 Docker volumes 中：

- PostgreSQL: `japan-antiwar-intel_japan_antiwar_postgres_data`
- Qdrant: `japan-antiwar-intel_japan_antiwar_qdrant_data`
- FreshRSS: `japan-antiwar-intel_japan_antiwar_freshrss_data`
- n8n: `japan-antiwar-intel_japan_antiwar_n8n_data`

報告檔案在專案目錄的 `reports/` 下。

## 公眾討論層

在圖形控制台打開「公眾討論」頁籤，選擇日期、平台、關鍵詞和抓取數量後點選「抓取公開討論並入庫」。頁面會即時顯示當前平台、找到的貼文、評論數量和規則情緒初判。

預設公開來源：

- **Reddit**：`r/japan`、`r/okinawa`、`r/japanlife`、`r/newsokur` 的公開 JSON 搜索和公開評論。僅讀取公開 API 和 old.reddit.com 頁面，不登入、不繞過任何存取限制。

公眾討論資料單獨寫入 `discussion_posts`、`discussion_comments`、`discussion_sentiments`、`discussion_fetch_runs`，不會混入新聞文章表。週報中的「公眾討論與情緒信號」只描述輿論、敘事和情緒樣本，不能作為事實判斷依據，也不能提高重大判斷的證據等級。

## 分析視角

系統的分析框架關注日本反戰運動的以下維度：

- **憲法第九條**：修憲動態、護憲運動、和平憲法相關公開討論。
- **美軍基地**：沖繩及其他地區美軍基地相關的抗議、搬遷、環境與安全問題。
- **和平運動**：市民團體、學生運動、勞工組織的反戰活動與聲明。
- **核議題**：核廢棄、核武禁止條約、廣島/長崎相關和平倡議。
- **外交與安全政策**：日美安保條約、防衛預算、集體自衛權相關公開辯論。
