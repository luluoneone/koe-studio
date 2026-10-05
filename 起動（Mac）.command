#!/bin/zsh
# Koe Studio を起動する（ダブルクリック用）。止めるときはこのウィンドウで Ctrl+C。
cd "$(dirname "$0")" || exit 1
UV="$HOME/.local/bin/uv"
[ -x "$UV" ] || UV="$(command -v uv 2>/dev/null)"
if [ -z "$UV" ]; then
  echo "動かすのに必要な「uv」（Python の実行ツール）が入っていません。"
  echo "公式の手順（https://docs.astral.sh/uv/）で今すぐ入れますか？ [y/N]"
  read -r ans
  if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then echo "中止しました。"; read -k 1; exit 1; fi
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "uv を入れられませんでした。"; read -k 1; exit 1; }
  UV="$HOME/.local/bin/uv"
fi
"$UV" run -q app.py
code=$?  # zsh では status は書き換えられない名前なので使わない
# Ctrl+C で止めたとき（130）は正常な終了として扱う
if [ $code -ne 0 ] && [ $code -ne 130 ]; then echo "終了コード $code で止まりました。上のメッセージを確認してください。"; read -k 1; fi
