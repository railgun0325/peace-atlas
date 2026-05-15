from __future__ import annotations

from typing import Any

MAPPING: dict[str, str] = {
    # Prefectures
    "北海道": "北海道", "Hokkaido": "北海道", "Hokkaidō": "北海道",
    "青森": "青森県", "青森県": "青森県", "Aomori": "青森県",
    "岩手": "岩手県", "岩手県": "岩手県", "Iwate": "岩手県",
    "宮城": "宮城県", "宮城県": "宮城県", "Miyagi": "宮城県",
    "秋田": "秋田県", "秋田県": "秋田県", "Akita": "秋田県",
    "山形": "山形県", "山形県": "山形県", "Yamagata": "山形県",
    "福島": "福島県", "福島県": "福島県", "Fukushima": "福島県",
    "茨城": "茨城県", "茨城県": "茨城県", "Ibaraki": "茨城県",
    "栃木": "栃木県", "栃木県": "栃木県", "Tochigi": "栃木県",
    "群馬": "群馬県", "群馬県": "群馬県", "Gunma": "群馬県",
    "埼玉": "埼玉県", "埼玉県": "埼玉県", "Saitama": "埼玉県",
    "千葉": "千葉県", "千葉県": "千葉県", "Chiba": "千葉県",
    "東京": "東京都", "東京都": "東京都", "Tokyo": "東京都", "Tokio": "東京都",
    "神奈川": "神奈川県", "神奈川県": "神奈川県", "Kanagawa": "神奈川県",
    "新潟": "新潟県", "新潟県": "新潟県", "Niigata": "新潟県",
    "富山": "富山県", "富山県": "富山県", "Toyama": "富山県",
    "石川": "石川県", "石川県": "石川県", "Ishikawa": "石川県",
    "福井": "福井県", "福井県": "福井県", "Fukui": "福井県",
    "山梨": "山梨県", "山梨県": "山梨県", "Yamanashi": "山梨県",
    "長野": "長野県", "長野県": "長野県", "Nagano": "長野県",
    "岐阜": "岐阜県", "岐阜県": "岐阜県", "Gifu": "岐阜県",
    "静岡": "静岡県", "静岡県": "静岡県", "Shizuoka": "静岡県",
    "愛知": "愛知県", "愛知県": "愛知県", "Aichi": "愛知県",
    "三重": "三重県", "三重県": "三重県", "Mie": "三重県",
    "滋賀": "滋賀県", "滋賀県": "滋賀県", "Shiga": "滋賀県",
    "京都": "京都府", "京都府": "京都府", "Kyoto": "京都府", "Kyōto": "京都府",
    "大阪": "大阪府", "大阪府": "大阪府", "Osaka": "大阪府", "Ōsaka": "大阪府",
    "兵庫": "兵庫県", "兵庫県": "兵庫県", "Hyogo": "兵庫県", "Hyōgo": "兵庫県",
    "奈良": "奈良県", "奈良県": "奈良県", "Nara": "奈良県",
    "和歌山": "和歌山県", "和歌山県": "和歌山県", "Wakayama": "和歌山県",
    "鳥取": "鳥取県", "鳥取県": "鳥取県", "Tottori": "鳥取県",
    "島根": "島根県", "島根県": "島根県", "Shimane": "島根県",
    "岡山": "岡山県", "岡山県": "岡山県", "Okayama": "岡山県",
    "広島": "広島県", "広島県": "広島県", "Hiroshima": "広島県",
    "山口": "山口県", "山口県": "山口県", "Yamaguchi": "山口県",
    "徳島": "徳島県", "徳島県": "徳島県", "Tokushima": "徳島県",
    "香川": "香川県", "香川県": "香川県", "Kagawa": "香川県",
    "愛媛": "愛媛県", "愛媛県": "愛媛県", "Ehime": "愛媛県",
    "高知": "高知県", "高知県": "高知県", "Kochi": "高知県", "Kōchi": "高知県",
    "福岡": "福岡県", "福岡県": "福岡県", "Fukuoka": "福岡県",
    "佐賀": "佐賀県", "佐賀県": "佐賀県", "Saga": "佐賀県",
    "長崎": "長崎県", "長崎県": "長崎県", "Nagasaki": "長崎県",
    "熊本": "熊本県", "熊本県": "熊本県", "Kumamoto": "熊本県",
    "大分": "大分県", "大分県": "大分県", "Oita": "大分県", "Ōita": "大分県",
    "宮崎": "宮崎県", "宮崎県": "宮崎県", "Miyazaki": "宮崎県",
    "鹿児島": "鹿児島県", "鹿児島県": "鹿児島県", "Kagoshima": "鹿児島県",
    "沖縄": "沖縄県", "沖縄県": "沖縄県", "Okinawa": "沖縄県",

    # Major cities (canonical → prefecture.city)
    "名古屋": "愛知県名古屋市", "Nagoya": "愛知県名古屋市",
    "横浜": "神奈川県横浜市", "横浜市": "神奈川県横浜市", "Yokohama": "神奈川県横浜市",
    "那覇": "沖縄県那覇市", "那覇市": "沖縄県那覇市", "Naha": "沖縄県那覇市",
    "宜野湾": "沖縄県宜野湾市", "宜野湾市": "沖縄県宜野湾市", "Ginowan": "沖縄県宜野湾市",
    "名護": "沖縄県名護市", "名護市": "沖縄県名護市", "Nago": "沖縄県名護市",
    "辺野古": "沖縄県名護市辺野古", "Henoko": "沖縄県名護市辺野古",
    "普天間": "沖縄県宜野湾市普天間", "Futenma": "沖縄県宜野湾市普天間",
    "嘉手納": "沖縄県嘉手納町", "Kadena": "沖縄県嘉手納町",
    "広島市": "広島県広島市", "Hiroshima City": "広島県広島市",
    "長崎市": "長崎県長崎市", "Nagasaki City": "長崎県長崎市",
    "福岡市": "福岡県福岡市", "Fukuoka City": "福岡県福岡市",
    "札幌": "北海道札幌市", "札幌市": "北海道札幌市", "Sapporo": "北海道札幌市",
    "仙台": "宮城県仙台市", "仙台市": "宮城県仙台市", "Sendai": "宮城県仙台市",

    # Major military / base areas
    "横須賀": "神奈川県横須賀市", "Yokosuka": "神奈川県横須賀市",
    "佐世保": "長崎県佐世保市", "Sasebo": "長崎県佐世保市",
    "岩国": "山口県岩国市", "Iwakuni": "山口県岩国市",
    "三沢": "青森県三沢市", "Misawa": "青森県三沢市",
    "座間": "神奈川県座間市", "Zama": "神奈川県座間市",
    "横田": "東京都福生市", "Yokota": "東京都福生市",

    # Japan variants
    "日本": "日本", "Japan": "日本", "Nippon": "日本", "Nihon": "日本",
    "全国": "日本全国", "日本全国": "日本全国",

    # Regions
    "関東": "関東地方", "Kanto": "関東地方", "Kantō": "関東地方",
    "関西": "関西地方", "Kansai": "関西地方", "近畿": "関西地方",
    "九州": "九州地方", "Kyushu": "九州地方", "Kyūshū": "九州地方",
    "東北": "東北地方", "Tohoku": "東北地方", "Tōhoku": "東北地方",
    "中部": "中部地方", "Chubu": "中部地方", "Chūbu": "中部地方",
    "中国": "中国地方", "Chugoku": "中国地方", "Chūgoku": "中国地方",
    "四国": "四国地方", "Shikoku": "四国地方",
    "北陸": "北陸地方", "Hokuriku": "北陸地方",
    "北海道地方": "北海道", "Hokkaido region": "北海道",

    # Disputed / variant
    "尖閣諸島": "尖閣諸島", "尖閣": "尖閣諸島", "Senkaku": "尖閣諸島",
    "Senkaku Islands": "尖閣諸島",
    "竹島": "竹島", "Takeshima": "竹島",
    "北方領土": "北方領土", "北方四島": "北方領土", "Northern Territories": "北方領土",
    "南西諸島": "南西諸島", "Nansei": "南西諸島",
    "先島諸島": "先島諸島", "Sakishima": "先島諸島",
    "与那国": "沖縄県与那国町", "Yonaguni": "沖縄県与那国町",
    "宮古島": "沖縄県宮古島市", "宮古": "沖縄県宮古島市", "Miyakojima": "沖縄県宮古島市",
    "石垣島": "沖縄県石垣市", "石垣": "沖縄県石垣市", "Ishigaki": "沖縄県石垣市",
}


def normalize(name: str) -> str | None:
    """Normalize a Japanese place name to its canonical form.

    Returns None if the name cannot be mapped, canonical string otherwise.
    """
    if not name or not name.strip():
        return None
    key = name.strip()
    return MAPPING.get(key)


def normalize_or_original(name: str) -> str:
    """Normalize if possible, return original string otherwise."""
    result = normalize(name)
    return result if result is not None else name.strip()


def search(text: str) -> list[dict[str, Any]]:
    """Find all recognized place names in text, returning canonical forms."""
    if not text:
        return []
    found: dict[str, dict[str, Any]] = {}
    for variant, canonical in MAPPING.items():
        if variant.lower() in text.lower() or variant in text:
            if canonical not in found:
                found[canonical] = {
                    "matched_text": variant,
                    "canonical_name": canonical,
                }
            else:
                if len(variant) > len(found[canonical]["matched_text"]):
                    found[canonical]["matched_text"] = variant
    return sorted(found.values(), key=lambda item: len(item["matched_text"]), reverse=True)


def prefecture_only(canonical: str) -> str:
    """Extract just the prefecture name from a canonical place string."""
    for pref_kanji in [
        "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
        "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
        "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
        "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
        "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
        "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
        "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
    ]:
        if canonical.startswith(pref_kanji):
            return pref_kanji
    return canonical
