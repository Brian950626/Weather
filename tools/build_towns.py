"""由 taiwan-atlas 的鄉鎮市區界線算出各鄉鎮的中心點,產生 weekcast/towns.py。

資料來源:https://github.com/dkaoster/taiwan-atlas (MIT 授權;界線取自內政部國土測繪中心
的開放資料)。取每個鄉鎮「面積最大的那一塊」的重心,離島或飛地不會把中心點拉到海上。

用法:python tools/build_towns.py towns-10t.json > weekcast/towns.py
"""

import json
import sys


def main(path: str) -> None:
    topo = json.load(open(path, encoding="utf-8"))
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)

    def ring(indices):
        out = []
        for i in indices:
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(pts if not out else pts[1:])
        return out

    def area_centroid(pts):
        a = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            cross = x0 * y1 - x1 * y0
            a += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if abs(a) < 1e-12:
            return 0.0, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        return abs(a) / 2, cx / (3 * a), cy / (3 * a)

    rows = []
    for g in topo["objects"]["towns"]["geometries"]:
        polygons = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"]
        _, lon, lat = max(area_centroid(ring(poly[0])) for poly in polygons)  # 每塊只看外環
        p = g["properties"]
        # 原始資料裡縣市寫「台」、部分鄉鎮寫「臺」;統一成「台」,搜尋時兩種寫法都接受
        name = lambda text: text.replace("臺", "台")
        rows.append((p["COUNTYCODE"], p["TOWNCODE"], name(p["COUNTYNAME"]), name(p["TOWNNAME"]), round(lat, 3), round(lon, 3)))
    rows.sort()
    print('"""臺灣 368 個鄉鎮市區的中心點(縣市, 鄉鎮市區, 緯度, 經度)。')
    print()
    print("由 tools/build_towns.py 產生,請勿手動修改。界線資料來自 taiwan-atlas(MIT 授權),")
    print('原始來源是內政部國土測繪中心的開放資料。')
    print('"""')
    print()
    print("TOWNS = (")
    for _, _, county, town, lat, lon in rows:
        print(f'    ("{county}", "{town}", {lat}, {lon}),')
    print(")")


if __name__ == "__main__":
    main(sys.argv[1])
