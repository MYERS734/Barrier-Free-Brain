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


def extract_amount_from_text(text: str):
    """OCRで読み取ったテキストから『合計』金額だけを探す簡易ロジック。

    品目ごとの自動分類はレシートのレイアウト次第で誤読が多いため行わない。
    『合計』という文字を含む行が見つかった場合のみ、その行の数字を返す。
    見つからなければ 0 を返し、ユーザーが必ず目で見て手入力する前提にする
    （不確かな推測値を初期値として出すと、逆にミスを誘発するため）。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for line in lines:
        if "合計" in line and "預" not in line and "釣" not in line:
            nums = re.findall(r"[\d,]{2,}", line)
            cleaned = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
            if cleaned:
                return cleaned[0]

    return 0  # 見つからない場合は0。ユーザーに生テキストを見ながら入力してもらう。


# --- 3. 【実際のOCR ＋ 合計金額の自動抽出】 ---
def analyze_receipt_image(uploaded_file) -> dict:
    image = Image.open(uploaded_file)

    base_width = 800
    w_percent = base_width / float(image.size[0])
    h_size = int(float(image.size[1]) * w_percent)
    image_resized = image.resize((base_width, h_size), Image.Resampling.LANCZOS)

    extracted_text = pytesseract.image_to_string(image_resized, lang="jpn+eng")

    total_amount = extract_amount_from_text(extracted_text)

    cleaned_data = {
        "date": "",  # OCRでの日付抽出は誤読が多いため、ユーザーに確認してもらう
        "total_amount": total_amount,
        "shop_info": "",
        "memo": "",
        "debit_account": "食費（毎日のごはん・おやつ）",
        "raw_text": extracted_text,  # 確認用に生テキストも保持
    }
    return cleaned_data


def parsed_data_to_csv_row(data: dict) -> list:
    """確定したデータをCSVの1行分に変換する。"""
    return [[
        data.get("date", ""),
        data.get("shop_info", ""),
        data.get("memo", ""),
        data.get("total_amount", 0),
        data.get("debit_account", ""),
    ]]


def build_csv_bytes(history: list) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日付", "店名", "メモ", "金額", "勘定科目（カテゴリ）"])
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

    if st.session_state.parsed_data["total_amount"] == 0:
        st.warning("⚠️ 自動では金額を見つけられませんでした。下の生テキストを見ながら、合計金額を入力してください。")
    else:
        st.write("写真から自動で読み取った金額です。レシートと見比べて、違っていたら直してください。")

    with st.expander("🔍 OCRが読み取った生のテキストを見る（参考）", expanded=(st.session_state.parsed_data["total_amount"] == 0)):
        st.text(st.session_state.parsed_data.get("raw_text", ""))

    amount = st.number_input(
        "今回のお買い物の合計金額（円）",
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
    st.subheader("ステップ 3: お店とカテゴリの確認")
    st.info("🧠 焦らず、あなたのペースで選んでくださいね。")

    date = st.text_input(
        "日付（例: 2026-06-21）",
        value=st.session_state.parsed_data.get("date", ""),
    )
    shop = st.text_input(
        "お店の名前",
        value=st.session_state.parsed_data.get("shop_info", ""),
    )
    memo = st.text_input(
        "メモ（買ったものなど。省略できます）",
        value=st.session_state.parsed_data.get("memo", ""),
    )

    st.write("---")
    st.write(f"💰 **合計金額：{st.session_state.parsed_data['total_amount']}円**")

    default_index = ACCOUNT_OPTIONS.index(st.session_state.parsed_data["debit_account"]) \
        if st.session_state.parsed_data["debit_account"] in ACCOUNT_OPTIONS else 0
    selected_account = st.selectbox(
        "🛠️ この支払いはどのグループですか？",
        options=ACCOUNT_OPTIONS,
        index=default_index,
    )

    st.session_state.parsed_data["date"] = date
    st.session_state.parsed_data["shop_info"] = shop
    st.session_state.parsed_data["memo"] = memo
    st.session_state.parsed_data["debit_account"] = selected_account

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
