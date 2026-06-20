import streamlit as st
import json
import re
import io
import csv
import pytesseract
from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()

# --- 1. ページ全体の初期設定（文字を大きく、見やすく） ---
st.set_page_config(page_title="Barrier-Free Brain", layout="centered")

st.markdown("""
    <style>
    html, body, [class*="css"] { font-size: 18px !important; }
    .stButton>button { width: 100%; font-size: 20px; height: 3em; }
    </style>
""", unsafe_allow_html=True)

st.title("🧠 バリアフリーブレイン")
st.caption("特性に寄り添う、焦らせない・ミスしない「自分専用」家計簿システム")

# --- 2. 状態（ステート）の管理 ---
if "step" not in st.session_state:
    st.session_state.step = "ocr_input"
if "parsed_data" not in st.session_state:
    st.session_state.parsed_data = None
if "history" not in st.session_state:
    st.session_state.history = []  # CSV出力用に確定済みデータを積み重ねる

# カテゴリ候補（家計簿用の分類リスト。優しい言い回し＋補足つき）
ACCOUNT_OPTIONS = [
    "食費（毎日のごはん・おやつ）",
    "日用品費（消耗品・雑貨）",
    "交通費（電車・バス・ガソリン）",
    "交際費（プレゼント・友人との外食）",
    "エンタメ・趣味（本・ゲーム・娯楽）",
    "その他",
]

# シンプルなキーワード分類（あとでAI APIに差し替え可能な構造にしてある）
CATEGORY_KEYWORDS = {
    "食費（毎日のごはん・おやつ）": ["ごはん", "弁当", "パン", "飲料", "お茶", "コーヒー", "弁", "食品", "スナック", "菓子"],
    "日用品費（消耗品・雑貨）": ["洗剤", "ティッシュ", "トイレ", "シャンプー", "雑貨"],
    "交通費（電車・バス・ガソリン）": ["駅", "バス", "タクシー", "ガソリン", "ETC"],
}


def guess_category(item_name: str) -> str:
    """商品名からカテゴリを推測する簡易ロジック。当てはまらなければ食費をデフォルトにする。"""
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in item_name for kw in keywords):
            return category
    return "食費（毎日のごはん・おやつ）"


def extract_amount_from_text(text: str):
    """OCRで読み取ったテキストから金額を探す簡易ロジック。
    『合計』を含む行を最優先し、見つからなければ『¥』『円』を含む行から探す。
    """
    lines = text.splitlines()

    # 最優先：「合計」を含む行（「お預り」「お釣り」は合計ではないので除外）
    total_candidates = []
    for line in lines:
        if "合計" in line:
            nums = re.findall(r"[\d,]{3,}", line)
            total_candidates += [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
    if total_candidates:
        return total_candidates[0]

    # 次点：「¥」「円」を含むが「預り」「釣り」を含まない行
    fallback_candidates = []
    for line in lines:
        if ("¥" in line or "円" in line) and ("預" not in line and "釣" not in line):
            nums = re.findall(r"[\d,]{3,}", line)
            fallback_candidates += [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
    if fallback_candidates:
        return max(fallback_candidates)

    # 最終手段：テキスト全体から数字を探す
    nums = re.findall(r"[\d,]{3,}", text)
    cleaned_nums = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
    return max(cleaned_nums) if cleaned_nums else 0


def extract_lines_as_items(text: str):
    """OCRテキストの各行を『商品名らしき行』として扱い、雑に品目リストを作る簡易ロジック。"""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "合計" in line or "お釣り" in line or "預り" in line or "レジ" in line:
            continue
        nums = re.findall(r"[\d,]{3,}", line)
        if nums:
            price = int(nums[-1].replace(",", ""))
            name = re.sub(r"[\d,¥円]+", "", line).strip() or "品目不明"
            items.append({
                "name": name,
                "price": price,
                "debit_account": guess_category(name),
                "ai_reason": f"行内のキーワードから「{guess_category(name)}」と推定しました。",
            })
    if not items:
        items.append({
            "name": "品目を特定できませんでした（手動で入力してください）",
            "price": 0,
            "debit_account": "その他",
            "ai_reason": "OCRから品目を抽出できませんでした。",
        })
    return items


# --- 3. 【実際のOCR ＋ 家計簿自動分類】 ---
def analyze_receipt_image(uploaded_file) -> dict:
    image = Image.open(uploaded_file)

    base_width = 800
    w_percent = base_width / float(image.size[0])
    h_size = int(float(image.size[1]) * w_percent)
    image_resized = image.resize((base_width, h_size), Image.Resampling.LANCZOS)

    extracted_text = pytesseract.image_to_string(image_resized, lang="jpn+eng")

    total_amount = extract_amount_from_text(extracted_text)
    items = extract_lines_as_items(extracted_text)

    cleaned_data = {
        "date": "",  # ユーザーに確認してもらう（OCRでの日付抽出は誤読が多いため空にしておく）
        "total_amount": total_amount,
        "shop_info": "",
        "items": items,
        "raw_text": extracted_text,  # デバッグ・確認用に生テキストも保持
    }
    return cleaned_data


def parsed_data_to_csv_row(data: dict) -> list:
    """確定したデータをCSVの1行分に変換する。"""
    rows = []
    for item in data["items"]:
        rows.append([
            data.get("date", ""),
            data.get("shop_info", ""),
            item["name"],
            item["price"],
            item["debit_account"],
        ])
    return rows


def build_csv_bytes(history: list) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日付", "店名", "品目", "金額", "勘定科目（カテゴリ）"])
    for entry in history:
        for row in parsed_data_to_csv_row(entry):
            writer.writerow(row)
    # ExcelでもGoogleスプレッドシートでも文字化けしないようUTF-8 BOM付きにする
    return ("\ufeff" + output.getvalue()).encode("utf-8")


# --- 4. 画面の構築（ステップ式UI） ---

# 【ステップ1: 画像読み込み（アップロード対応）】
if st.session_state.step == "ocr_input":
    st.subheader("ステップ 1: レシートの読み込み")
    st.write("レシートの写真を選んでください（JPG・PNG・HEIC対応）。")

    uploaded_file = st.file_uploader(
        "レシート画像を選択",
        type=["jpg", "jpeg", "png", "heic", "heif"],
    )

    if uploaded_file is not None:
        st.image(uploaded_file, caption="読み込んだ画像", use_container_width=True)

        if st.button("📸 この画像を解析する"):
            with st.spinner("🧠 写真から文字を読み取っています..."):
                try:
                    st.session_state.parsed_data = analyze_receipt_image(uploaded_file)
                    st.session_state.step = "verify_amount"
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 解析中にエラーが発生しました: {e}")
    else:
        st.info("👆 まずは画像を選んでください。")

    if st.session_state.history:
        st.write("---")
        st.write(f"📚 これまでに記録した件数：{len(st.session_state.history)} 件")
        csv_bytes = build_csv_bytes(st.session_state.history)
        st.download_button(
            "⬇️ 今までの記録をCSVでダウンロード",
            data=csv_bytes,
            file_name="barrier_free_brain_kakeibo.csv",
            mime="text/csv",
        )

# 【ステップ2: 金額の確認（1画面1タスク）】
elif st.session_state.step == "verify_amount":
    st.subheader("ステップ 2: 金額の確認")
    st.write("写真から自動で読み取った金額です。違っていたら直してください。")

    with st.expander("🔍 OCRが読み取った生のテキストを見る（参考）"):
        st.text(st.session_state.parsed_data.get("raw_text", ""))

    amount = st.number_input(
        "今回のお買い物金額（円）",
        value=st.session_state.parsed_data["total_amount"],
        step=1,
        min_value=0,
    )
    st.session_state.parsed_data["total_amount"] = amount

    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬅️ 最初に戻る"):
            st.session_state.step = "ocr_input"
            st.rerun()
    with col2:
        if st.button("次へ進む ➡️"):
            st.session_state.step = "verify_details"
            st.rerun()

# 【ステップ3: その他の情報とカテゴリの最終確認】
elif st.session_state.step == "verify_details":
    st.subheader("ステップ 3: 分類（科目）の確認")
    st.info("🧠 自動で分けたカテゴリを確認・修正できます。焦らずあなたのペースで選んでくださいね。")

    date = st.text_input(
        "日付（例: 2026-06-21）",
        value=st.session_state.parsed_data.get("date", ""),
    )
    shop = st.text_input(
        "お店の名前",
        value=st.session_state.parsed_data.get("shop_info", ""),
    )

    st.write("---")
    st.write("🛒 **買ったものの一覧（タップして変更できます）:**")

    for i, item in enumerate(st.session_state.parsed_data["items"]):
        st.info(f"**商品名:** {item['name']} ({item['price']}円)\n\n🤖 判定理由: {item['ai_reason']}")

        new_name = st.text_input(
            f"品目名（{i+1}）", value=item["name"], key=f"name_{i}"
        )
        new_price = st.number_input(
            f"金額（{i+1}）", value=item["price"], step=1, min_value=0, key=f"price_{i}"
        )

        default_index = (
            ACCOUNT_OPTIONS.index(item["debit_account"])
            if item["debit_account"] in ACCOUNT_OPTIONS
            else len(ACCOUNT_OPTIONS) - 1
        )
        selected_account = st.selectbox(
            f"🛠️ '{item['name']}' の正しいグループを選んでください",
            options=ACCOUNT_OPTIONS,
            index=default_index,
            key=f"account_{i}",
        )

        st.session_state.parsed_data["items"][i]["name"] = new_name
        st.session_state.parsed_data["items"][i]["price"] = new_price
        st.session_state.parsed_data["items"][i]["debit_account"] = selected_account
        st.write("---")

    st.session_state.parsed_data["date"] = date
    st.session_state.parsed_data["shop_info"] = shop

    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬅️ 金額の確認に戻る"):
            st.session_state.step = "verify_amount"
            st.rerun()
    with col2:
        if st.button("✅ これで家計簿に記録する！"):
            st.session_state.history.append(st.session_state.parsed_data)
            st.session_state.step = "success"
            st.rerun()

# 【ステップ4: 完了画面】
elif st.session_state.step == "success":
    st.balloons()
    st.success("🎉 あなたの家計簿に記録されました！お疲れ様でした！")

    st.write("### 🗂️ 内部で作成されたデータ構造")
    st.json(st.session_state.history[-1])

    csv_bytes = build_csv_bytes(st.session_state.history)
    st.download_button(
        "⬇️ 全件をCSVでダウンロード",
        data=csv_bytes,
        file_name="barrier_free_brain_kakeibo.csv",
        mime="text/csv",
    )

    if st.button("🔄 別のレシートを登録する"):
        st.session_state.step = "ocr_input"
        st.session_state.parsed_data = None
        st.rerun()
