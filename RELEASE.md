# 發布 `reachy_mini`

版本發布是由 **Release** 工作流程（`.github/workflows/wheels.yml`）驅動，
這是一個帶有 `release_type` 下拉選單的單一 `workflow_dispatch`。版本號靜態存在於 `pyproject.toml`（唯一的真實來源）：`main` 分支帶有 `X.Y.Z.dev0`；每個 `vX.Y-release` 分支則帶有已發布的 `X.Y.Z`。

> **注意：** `main` 分支刻意採用 [PEP 440](https://peps.python.org/pep-0440/) 的 `.dev0` 版本格式（例如 `1.10.0.dev0`），這**不是**標準的語意化版本 (semver)。任何從 `pyproject.toml` 讀取版本的工具都必須能處理非 semver 的 PEP 440 格式（`.devN`、`rcN` 等）— 例如 npm-publish CI 在呼叫 `npm version` 前會先將版本正規化為 semver。

## 三種發布模式

| 模式 | 觸發來源 | 功能說明 |
|------|--------------|--------------|
| `dry-run` | 任何分支 | 唯讀行前檢查：檢查每個 secret/var 是否已設置、`RELEASE_PAT` 是否能推送至兩個儲存庫、`pypi` 環境是否存在、OpenCode + 模型 + HF token 是否可正常運作（微型即時呼叫測試），並預覽每個模式會產生的版本號。不打 tag、不發布、不開 PR。請務必先執行此模式。 |
| `minor-prerelease` | `main` | 讀取 `X.Y.Z.dev0` → 切出 `X.Y.Zrc<N>`（RC 從 1 開始），**將 `vX.Y-release` 重設為 `main`**（使每個 RC 都是全新快照）、打 tag、發布至 PyPI、重新生成 AI 發布說明，並發布/重新整理**此次要版本的 GitHub 預發布 (prerelease)**（每個次要版本維護一個 release：每個 RC 都會重新標記並更新其說明，因此 releases 頁面始終顯示最新的 RC 作為已發布的預發布版）。同時在 `reachy_mini_conversation_app` 開啟 RC 測試 PR。 |
| `minor-release` | `main` | 將最新 RC 晉升為正式版 `X.Y.Z`、發布至 PyPI、將該預發布版轉為正式 tag（標記為 *latest*，清除 prerelease 標記）、觸發文件建置，並開啟 PR 將 `main` 的版本遞增為 `X.(Y+1).0.dev0`。 |
| `patch-release` | `vX.Y-release` | 遞增修補版本號 (`X.Y.Z+1`)、打 tag、發布。不會標記為 *latest*。 |

典型流程：`minor-prerelease` → 驗證 RC（透過對話 App 中的 PR CI 以及手動測試）→ 若需要更多 RC 則再次執行 `minor-prerelease` → `minor-release`。
已發布次要版本的錯誤修復：cherry-pick 至 `vX.Y-release`，然後從該分支執行 `patch-release`。

請務必先執行 `dry-run` 以確認 secrets/vars/權限皆已設置妥當。

## 工作流程圖與失敗復原

```
prepare ──▶ publish-pypi ──┬──▶ publish-npm      (在該 tag 觸發 npm 工作流程)
                           ├──▶ release-notes
                           ├──▶ test-downstream   (僅 prerelease)
                           └──▶ post-release       (僅 minor-release)
```

`prepare` 會在發布**之前**先推送版本遞增的 commit **與 tag**，因此當發布開始時 tag 已經存在。`prepare` 之後的所有步驟都依賴於成功的 `publish-pypi`：如果發布失敗，就不會發布/晉升 GitHub release，也不會開啟 RC 測試 PR 與版本遞增 PR — 整個流水線將會停止。

**如果 `publish-pypi` 失敗：** 請**不要**重新觸發整個工作流程（`prepare` 會因為 tag 已存在而報錯）。請改為**從 Actions UI 重新執行 `publish-pypi` 任務**（點擊「Re-run failed jobs」）；一旦成功，`release-notes` 與後續任務會接著繼續執行，不需要重新打 tag。如果 tag 或分支有誤且必須從頭開始，請在重新觸發前先刪除該 tag（以及剛建立的 `vX.Y-release` 分支）。

## 發布分支 (Release branch)

`vX.Y-release` 分支在**每次執行 `minor-prerelease` 時都會重設為 `main`**（強制推送），因此每個 RC 發布的內容完全等同於該時間點的 `main`。直接提交到發布分支上的任何修改都會被丟棄 — 請將修復合併到 `main` 後再切出新的 RC。

`minor-release` 和 `patch-release` **不會**重設：它們會以最後一個 RC 留下來的狀態建置，因此最終發布的內容就是你實際測試過的程式碼。

## 一次性設定 (One-time setup)

- **PyPI Trusted Publisher**（針對專案 `reachy-mini`）：儲存庫 `pollen-robotics/reachy_mini`，工作流程 `wheels.yml`。（OIDC — 不儲存 token。）工作流程特意命名為 `wheels.yml` 是為了重用現有的 Trusted Publisher（無環境限制），因此不需要修改 PyPI 設定。若日後重新命名為 `release.yml`，請先在 PyPI 上新增對應的 Trusted Publisher 項目。
- Repo 設定中的 **`pypi` GitHub Environment**；新增必要的審查者 (reviewers) 來管制發布。
- **Secret `RELEASE_PAT`** — 具有 `reachy_mini` 和 `reachy_mini_conversation_app` **兩者**的 `contents:write` + `pull_requests:write` 權限的 PAT 或 GitHub App token（用於開啟 RC 測試 PR 與發布後的版本遞增 PR，以便觸發其 CI）。
- **Secret `RELEASE_NOTES_HF_TOKEN`** — 具有 Inference Providers 權限範圍的 HF token。
- **Var `RELEASE_NOTES_MODEL`** — 例如 `huggingface/zai-org/GLM-5.2`。
- **Var `OPENCODE_VERSION`** — CI 中安裝的 OpenCode 固定版本。
- 在 `reachy_mini_conversation_app` 中新增 `rc-testing` 標籤（可選；即使沒有也能正常開啟 PR）。

## 發布說明 (AI 自動起草)

`utils/release_notes/` 移植了 huggingface_hub 的「信任但驗證 (trust-but-verify)」生成器：

1. `fetch_prs.py` — 列出前一個 tag 之後合併的所有 PR（基準清單）。
2. OpenCode 透過 `.opencode/skills/reachy-mini-release-notes` skill 起草說明。
3. `validate_notes.py` — 檢查清單中的每個 PR 是否都有列出且沒有多餘的 PR；協調器會循環修正差異（最多 3 次迭代）。

在本地執行以進行預覽：

```bash
export GITHUB_TOKEN=...            # repo 讀取權限
export HF_TOKEN=...                # Inference Providers
export RELEASE_NOTES_MODEL=huggingface/zai-org/GLM-5.2
python -m utils.release_notes.generate_release_notes --since v1.9.0 --minor
# → .release-notes/RELEASE_NOTES_v1.10.0.md
```

在執行 `minor-release` 之前，GitHub 預發布版是可以編輯的 — 可以在那裡微調文字語氣。

**你的手動編輯在後續的 RC 中會被保留。** 某個次要版本的第一個 RC 會從頭生成說明。之後的每個 RC 會以目前 release 的內容為基準（`--seed`），只執行驗證迴圈：追加新合併的 PR 並移除失效的 PR — 周圍的文案（包括任何你手動修改的內容）都不會被更動。如果在上一個 RC 之後沒有新的 PR 合併，驗證會立即通過且完全不會呼叫模型。

`minor-release` 永遠不會重新生成：它會原封不動地直接發布內容，因此你最後編輯的內容就是最終發布的版本。

**編輯時的一點注意事項：** 驗證機制會在說明中搜尋 `#<數字>`，並將每個符合項視為必須存在於本次發布中的 PR。因此，如果引用的內容*不屬於*本次發布（例如 issue 編號、其他 repo 的 PR，或「感謝，請見 #999」），就會被歸類為多餘項目，且在下一個 RC 的修復步驟中**包含該字樣的整行都會被刪除**。

一般的純文字敘述是安全的，自動生成的 `by @author in #1234` 也是安全的。若要引用 issue，請使用完整網址（`https://github.com/pollen-robotics/reachy_mini/issues/1338`）：顯示效果相同，但正則表達式只會匹配獨立的 `#` 加上數字。在最後一個 RC 之後進行的編輯則沒有此限制，因為 `minor-release` 絕不會重新生成。
