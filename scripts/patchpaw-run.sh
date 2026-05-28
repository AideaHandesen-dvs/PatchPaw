#!/usr/bin/env bash
#
# patchpaw-run.sh — PatchPaw を使った自走実装ランナー
#
# tasks.txt のタスクを上から順に patchpaw fix へ投げる。
# 「パッチ生成 → 検証 → 適用 → テスト → 自己修正」のループは
# PatchPaw 内蔵 (controller.py) に任せ、このスクリプトはタスクの
# 連鎖・ログ・git 節目・停止条件だけを担当する。
#
# 使い方:
#   ./patchpaw-run.sh                     # カレントディレクトリで tasks.txt を流す
#   ./patchpaw-run.sh mytasks.txt         # 別のタスクファイルを指定
#   DRY_RUN=1 ./patchpaw-run.sh           # 実行せず、何が走るかだけ表示
#   STOP_ON_FAIL=0 ./patchpaw-run.sh      # 失敗しても次のタスクへ進む
#   MAX_ITER=3 ./patchpaw-run.sh          # LLM の最大試行回数を変更
#
# プロジェクト設定:
#   テストコマンドは以下の優先順で決まる:
#     1. 環境変数 PATCHPAW_TEST_CMD
#     2. .patchpaw/test-cmd ファイルの中身
#     3. デフォルト: python3 -m pytest tests/ -v --tb=short
#
set -uo pipefail
export PATCHPAW_QUIET=1

# ===== 挙動の設定 (環境変数で上書き可) =====
TASKS_FILE="${1:-tasks.txt}"
REPO_DIR="${REPO_DIR:-.}"
STOP_ON_FAIL="${STOP_ON_FAIL:-1}"
COMMIT_PER_TASK="${COMMIT_PER_TASK:-1}"
DRY_RUN="${DRY_RUN:-0}"
MAX_ITER="${MAX_ITER:-5}"

# テストコマンドの解決
if [ -n "${PATCHPAW_TEST_CMD:-}" ]; then
  TEST_CMD="$PATCHPAW_TEST_CMD"
elif [ -f ".patchpaw/test-cmd" ]; then
  TEST_CMD="$(cat .patchpaw/test-cmd)"
else
  TEST_CMD="python3 -m pytest tests/ -v --tb=short"
fi
# ===========================================

# --- ログ用の色付きヘルパ ---
c_reset=$'\033[0m'; c_bold=$'\033[1m'
c_blue=$'\033[34m'; c_green=$'\033[32m'; c_red=$'\033[31m'; c_yellow=$'\033[33m'
log()  { printf '%s▶ %s%s\n' "$c_blue"   "$*" "$c_reset"; }
ok()   { printf '%s✓ %s%s\n' "$c_green"  "$*" "$c_reset"; }
err()  { printf '%s✗ %s%s\n' "$c_red"    "$*" "$c_reset" >&2; }
warn() { printf '%s! %s%s\n' "$c_yellow" "$*" "$c_reset"; }

# --- Ctrl-C で綺麗に止まる ---
trap 'echo; err "中断された。PatchPaw のセッションログは sessions/ に残っている。"; exit 130' INT

# ===== 前提チェック =====
command -v patchpaw >/dev/null 2>&1 || { err "patchpaw が見つからない → cd ~/patchpaw && pip install -e ."; exit 1; }
[ -f "$TASKS_FILE" ] || { err "タスクファイルが無い: $TASKS_FILE"; exit 1; }
[ -n "${DEEPSEEK_API_KEY:-}" ] || warn "DEEPSEEK_API_KEY が未設定。export してあるか確認しろ。"

# git リポジトリの中か確認 (git連携は任意)
HAS_GIT=0
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  HAS_GIT=1
  if ! git diff --quiet || ! git diff --cached --quiet; then
    warn "作業ツリーに未コミットの変更がある。自走前に commit しておくと巻き戻しが楽。"
  fi
fi

# ===== タスク読み込み (# コメント行と空行は無視) =====
mapfile -t TASKS < <(grep -vE '^[[:space:]]*(#|$)' "$TASKS_FILE")
total=${#TASKS[@]}
[ "$total" -gt 0 ] || { err "$TASKS_FILE に実行可能なタスクが無い"; exit 1; }

log "タスク数: $total  (file: $TASKS_FILE)"
log "テストコマンド: $TEST_CMD"
log "最大試行回数: $MAX_ITER"
log "リポジトリ: $(cd "$REPO_DIR" && pwd)"
[ "$DRY_RUN" = "1" ] && warn "DRY-RUN モード: 実際には実行しない"
echo

# ===== 実行ループ =====
declare -a FAILED=()
i=0
for task in "${TASKS[@]}"; do
  i=$((i + 1))
  printf '%s━━━ [%d/%d] %s ━━━%s\n' "$c_bold" "$i" "$total" "$task" "$c_reset"

  if [ "$DRY_RUN" = "1" ]; then
    echo "  (dry-run) patchpaw fix \"$task\" --repo $REPO_DIR -y --test-cmd \"$TEST_CMD\" --max-iter $MAX_ITER"
    echo
    continue
  fi

  start=$(date +%s)

  # --- 自走本体 ---
  if patchpaw fix "$task" \
        --repo "$REPO_DIR" \
        --yes \
        --test-cmd "$TEST_CMD" \
        --max-iter "$MAX_ITER"
  then
    dur=$(( $(date +%s) - start ))
    ok "完了 (${dur}s): $task"

    # git 節目タグ
    if [ "$COMMIT_PER_TASK" = "1" ] && [ "$HAS_GIT" = "1" ]; then
      git add -A
      git commit -m "patchpaw: task $i - $task" --allow-empty -q
      tag="patchpaw-task-${i}-$(date +%Y%m%d-%H%M%S)"
      if git tag "$tag" >/dev/null 2>&1; then
        log "節目タグ: $tag  (git reset --hard $tag で戻せる)"
      fi
    fi
  else
    dur=$(( $(date +%s) - start ))
    err "失敗 (${dur}s): $task"
    FAILED+=("[$i] $task")
    if [ "$STOP_ON_FAIL" = "1" ]; then
      err "STOP_ON_FAIL=1 のため停止。sessions/ にログが残っている。"
      break
    else
      warn "STOP_ON_FAIL=0 のため次のタスクへ継続。"
    fi
  fi
  echo
done

# ===== サマリ =====
echo
printf '%s═══ サマリ ═══%s\n' "$c_bold" "$c_reset"
succeeded=$(( i - ${#FAILED[@]} ))
[ "$DRY_RUN" = "1" ] && { log "DRY-RUN 完了。実行されたタスクはありません。"; exit 0; }
ok "成功: ${succeeded} / ${total}"
if [ "${#FAILED[@]}" -gt 0 ]; then
  err "失敗: ${#FAILED[@]}"
  for f in "${FAILED[@]}"; do echo "    $f"; done
  exit 1
fi
ok "全タスク完了 🎉"
