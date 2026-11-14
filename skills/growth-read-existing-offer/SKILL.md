---
name: growth-read-existing-offer
description: "讀取既有App、銷售頁與文件，確認現在能賣什麼、不能承諾什麼。 用於已有產品的對外行銷與銷售：讀懂現有產品與可賣方案；可在完整流程中接力或獨立使用。"
---

# 讀懂現有產品與可賣方案

先讀 [詳細步驟、輸入與交付](references/procedure.md)，按本次目標執行。

使用宿主瀏覽器唯讀查看現有產品。產出 brand.facts：每一條都附來源、核對日期、限制。不可用歷史摘要冒充目前產品能力。

需要程式時使用相鄰 `growth-meta-ig-loop/scripts/growth.py`。先讀 [流程與接力操作](../growth-meta-ig-loop/references/workflows.md) 及 [本Skill交付格式](references/contract.json)。可用skill-init執行單項或workflow-init接整條流程；研究、判斷與平台工具由宿主Agent執行，共用程式保存真實結果、去重與恢復。每個品牌使用自己的設定、連接與工作資料，不綁任何範例帳號。

需要具體平台操作時，用channel-skills選對應渠道細項；具體目標用goals選循環，見 [目標與渠道工作](../growth-meta-ig-loop/references/goals.md)。本工作是可重用父契約。

跨Agent與常駐worker、graph、品牌記憶及實驗版本操作，讀 [平台執行契約](../growth-meta-ig-loop/references/platform.md)。改善分為操作可靠度、具體目標及經濟結果三種證據；金錢結論保留外部客戶及全成本要求。詳見 [現況組合與經驗學習](../growth-meta-ig-loop/references/adaptive-execution.md)。資料不足等待，不用AI自評當改善。

實際工具呼叫、原操作對帳與已驗章資料接入，讀 [實際執行操作](../growth-meta-ig-loop/references/real-execution.md)。工具註冊不代表已執行，模型產出也不代表平台回執。

沿用使用者本輪已確認的品牌與執行範圍。只處理對外行銷，不修改產品、銷售頁、價格或結帳。需要不可代辦的本人操作時，保留進度、具體說明並繼續其他分支。
