"""產生 weekcast/cities.py:世界主要城市的中文名稱與座標。

中文名稱與國名是人工整理的(臺灣慣用譯名);座標用英文名向 Open-Meteo 的地名服務
(資料來自 GeoNames)查詢,取指定國家裡人口最多的那一筆,避免同名的小地方。

用法:python tools/build_cities.py > weekcast/cities.py   (需要連網,約 3 分鐘)
"""

import json
import sys
import time
import urllib.parse
import urllib.request

COUNTRIES = {
    "JP": "日本", "KR": "南韓", "CN": "中國", "HK": "香港", "MO": "澳門", "MN": "蒙古", "VN": "越南", "TH": "泰國",
    "MY": "馬來西亞", "SG": "新加坡", "ID": "印尼", "PH": "菲律賓", "KH": "柬埔寨", "LA": "寮國", "MM": "緬甸",
    "IN": "印度", "PK": "巴基斯坦", "BD": "孟加拉", "LK": "斯里蘭卡", "NP": "尼泊爾", "AE": "阿拉伯聯合大公國",
    "QA": "卡達", "SA": "沙烏地阿拉伯", "IL": "以色列", "TR": "土耳其", "IR": "伊朗", "KZ": "哈薩克", "UZ": "烏茲別克",
    "GB": "英國", "IE": "愛爾蘭", "FR": "法國", "DE": "德國", "NL": "荷蘭", "BE": "比利時", "CH": "瑞士", "AT": "奧地利",
    "IT": "義大利", "ES": "西班牙", "PT": "葡萄牙", "GR": "希臘", "DK": "丹麥", "SE": "瑞典", "NO": "挪威", "FI": "芬蘭",
    "IS": "冰島", "PL": "波蘭", "CZ": "捷克", "HU": "匈牙利", "RO": "羅馬尼亞", "UA": "烏克蘭", "RU": "俄羅斯",
    "HR": "克羅埃西亞", "US": "美國", "CA": "加拿大", "MX": "墨西哥", "CU": "古巴", "PA": "巴拿馬", "CO": "哥倫比亞",
    "PE": "祕魯", "CL": "智利", "AR": "阿根廷", "BR": "巴西", "EG": "埃及", "MA": "摩洛哥", "NG": "奈及利亞",
    "KE": "肯亞", "ET": "衣索比亞", "TZ": "坦尚尼亞", "ZA": "南非", "AU": "澳洲", "NZ": "紐西蘭", "FJ": "斐濟",
    "GU": "關島", "PW": "帛琉", "MV": "馬爾地夫",
}

# (中文名, 查詢用英文名, 國碼)
CITIES = """
東京 Tokyo JP|大阪 Osaka JP|京都 Kyoto JP|名古屋 Nagoya JP|橫濱 Yokohama JP|神戶 Kobe JP|福岡 Fukuoka JP|札幌 Sapporo JP
仙台 Sendai JP|廣島 Hiroshima JP|那霸 Naha JP|金澤 Kanazawa JP|長野 Nagano JP|奈良 Nara JP|熊本 Kumamoto JP|鹿兒島 Kagoshima JP
石垣 Ishigaki JP|函館 Hakodate JP|新潟 Niigata JP|筑波 Tsukuba JP
首爾 Seoul KR|釜山 Busan KR|濟州 Jeju KR|仁川 Incheon KR|大邱 Daegu KR
北京 Beijing CN|上海 Shanghai CN|廣州 Guangzhou CN|深圳 Shenzhen CN|成都 Chengdu CN|重慶 Chongqing CN|武漢 Wuhan CN
西安 Xi'an CN|杭州 Hangzhou CN|南京 Nanjing CN|廈門 Xiamen CN|福州 Fuzhou CN|青島 Qingdao CN|哈爾濱 Harbin CN
昆明 Kunming CN|拉薩 Lhasa CN|烏魯木齊 Urumqi CN|天津 Tianjin CN|香港 Hong Kong HK|澳門 Macau MO|烏蘭巴托 Ulaanbaatar MN
河內 Hanoi VN|胡志明市 Ho Chi Minh City VN|峴港 Da Nang VN|曼谷 Bangkok TH|清邁 Chiang Mai TH|普吉 Phuket TH
吉隆坡 Kuala Lumpur MY|檳城 George Town MY|亞庇 Kota Kinabalu MY|新加坡 Singapore SG|雅加達 Jakarta ID|峇里島 Denpasar ID
馬尼拉 Manila PH|宿霧 Cebu City PH|金邊 Phnom Penh KH|暹粒 Siem Reap KH|永珍 Vientiane LA|仰光 Yangon MM
新德里 New Delhi IN|孟買 Mumbai IN|班加羅爾 Bengaluru IN|加爾各答 Kolkata IN|清奈 Chennai IN|喀拉蚩 Karachi PK
達卡 Dhaka BD|可倫坡 Colombo LK|加德滿都 Kathmandu NP|馬列 Male MV
杜拜 Dubai AE|阿布達比 Abu Dhabi AE|杜哈 Doha QA|利雅德 Riyadh SA|特拉維夫 Tel Aviv IL|耶路撒冷 Jerusalem IL
伊斯坦堡 Istanbul TR|安卡拉 Ankara TR|德黑蘭 Tehran IR|阿拉木圖 Almaty KZ|塔什干 Tashkent UZ
倫敦 London GB|曼徹斯特 Manchester GB|愛丁堡 Edinburgh GB|都柏林 Dublin IE|巴黎 Paris FR|里昂 Lyon FR|馬賽 Marseille FR
尼斯 Nice FR|柏林 Berlin DE|慕尼黑 Munich DE|法蘭克福 Frankfurt am Main DE|漢堡 Hamburg DE|科隆 Cologne DE
阿姆斯特丹 Amsterdam NL|布魯塞爾 Brussels BE|蘇黎世 Zurich CH|日內瓦 Geneva CH|維也納 Vienna AT
羅馬 Rome IT|米蘭 Milan IT|威尼斯 Venice IT|佛羅倫斯 Florence IT|拿坡里 Naples IT|馬德里 Madrid ES|巴塞隆納 Barcelona ES
里斯本 Lisbon PT|雅典 Athens GR|哥本哈根 Copenhagen DK|斯德哥爾摩 Stockholm SE|奧斯陸 Oslo NO|赫爾辛基 Helsinki FI
雷克雅維克 Reykjavik IS|華沙 Warsaw PL|布拉格 Prague CZ|布達佩斯 Budapest HU|布加勒斯特 Bucharest RO|基輔 Kyiv UA
莫斯科 Moscow RU|聖彼得堡 Saint Petersburg RU|海參崴 Vladivostok RU|札格瑞布 Zagreb HR
紐約 New York US|洛杉磯 Los Angeles US|舊金山 San Francisco US|西雅圖 Seattle US|芝加哥 Chicago US|波士頓 Boston US
華盛頓 Washington US|邁阿密 Miami US|休士頓 Houston US|達拉斯 Dallas US|丹佛 Denver US|拉斯維加斯 Las Vegas US
鳳凰城 Phoenix US|亞特蘭大 Atlanta US|費城 Philadelphia US|聖地牙哥 San Diego US|聖荷西 San Jose US|檀香山 Honolulu US
安克拉治 Anchorage US|奧蘭多 Orlando US|紐奧良 New Orleans US|塔拉哈西 Tallahassee US
多倫多 Toronto CA|溫哥華 Vancouver CA|蒙特婁 Montreal CA|渥太華 Ottawa CA|卡加利 Calgary CA
墨西哥城 Mexico City MX|坎昆 Cancun MX|哈瓦那 Havana CU|巴拿馬城 Panama City PA|波哥大 Bogota CO|利馬 Lima PE
聖地牙哥(智利) Santiago CL|布宜諾斯艾利斯 Buenos Aires AR|聖保羅 Sao Paulo BR|里約熱內盧 Rio de Janeiro BR|巴西利亞 Brasilia BR
開羅 Cairo EG|卡薩布蘭加 Casablanca MA|拉哥斯 Lagos NG|奈洛比 Nairobi KE|阿迪斯阿貝巴 Addis Ababa ET
三蘭港 Dar es Salaam TZ|約翰尼斯堡 Johannesburg ZA|開普敦 Cape Town ZA
雪梨 Sydney AU|墨爾本 Melbourne AU|布里斯本 Brisbane AU|伯斯 Perth AU|阿得雷德 Adelaide AU|坎培拉 Canberra AU
達爾文 Darwin AU|凱恩斯 Cairns AU|黃金海岸 Gold Coast AU|奧克蘭 Auckland NZ|威靈頓 Wellington NZ|基督城 Christchurch NZ
皇后鎮 Queenstown NZ|蘇瓦 Suva FJ|關島 Hagatna GU|帛琉 Koror PW
"""


def lookup(english: str, code: str):
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": english, "count": 10, "language": "en"})
    request = urllib.request.Request(url, headers={"User-Agent": "weekcast-build/1"})
    for attempt in range(3):
        try:
            results = json.load(urllib.request.urlopen(request, timeout=30)).get("results") or []
            break
        except OSError:
            time.sleep(3 * (attempt + 1))
    else:
        return None
    same = [r for r in results if r.get("country_code") == code]
    return max(same, key=lambda r: r.get("population") or 0) if same else None


def main() -> None:
    rows, missing = [], []
    for item in CITIES.replace("\n", "|").split("|"):
        if not item.strip():
            continue
        chinese, rest = item.strip().split(" ", 1)
        english, code = rest.rsplit(" ", 1)
        hit = lookup(english, code)
        if hit is None:
            missing.append(chinese)
            continue
        rows.append((chinese, hit["name"], COUNTRIES[code], round(hit["latitude"], 3), round(hit["longitude"], 3)))
        time.sleep(0.15)
    if missing:
        print("找不到:" + "、".join(missing), file=sys.stderr)
    print('"""世界主要城市(中文名, 英文名, 國家, 緯度, 經度)。')
    print()
    print("由 tools/build_cities.py 產生,請勿手動修改;要增減城市請改那個檔案後重新產生。")
    print("中文名稱是人工整理的臺灣慣用譯名,座標來自 Open-Meteo 的地名服務(GeoNames 資料)。")
    print('"""')
    print()
    print("CITIES = (")
    for chinese, english, country, lat, lon in rows:
        print(f'    ("{chinese}", "{english}", "{country}", {lat}, {lon}),')
    print(")")


if __name__ == "__main__":
    main()
