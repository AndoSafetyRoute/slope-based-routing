  # app.py — 용산구 접근성 경로 탐색 Flask 서버                                                                                             
                                                                                                                                            
  from flask import Flask, request, jsonify, send_from_directory                                                                            
  from flask_cors import CORS                                                                                                                 
  import os, sys, re                                                                                                                        
  import requests as req                                                                                                                    
  from concurrent.futures import ThreadPoolExecutor, as_completed

  sys.path.insert(0, os.path.dirname(__file__))
  from accessibility_routing import (
      build_graph, find_route, USER_TYPES
  )

  app = Flask(__name__, static_folder="static")
  CORS(app)

  # 카카오 REST API 키 (환경변수 KAKAO_REST_API_KEY로 설정)
  KAKAO_KEY    = os.environ.get("KAKAO_REST_API_KEY", "")
  KAKAO_CENTER = {"x": "126.9905", "y": "37.5326"}   # 용산구 중심

  # 서버 시작 시 그래프 1회 로딩
  GEOJSON_PATH = os.path.join(os.path.dirname(__file__), "elevationcostroad.geojson")
  print("그래프 로딩 중... (최초 1회)")
  G = build_graph(GEOJSON_PATH)
  print("그래프 로딩 완료")


  def classify_zone(slope_pct: float, direction: str, user_type: str) -> str:
      # 유형별 기준으로 구간 존 분류: flat / optimal / warning / forbidden
      if direction == "flat":
          return "flat"
      params     = USER_TYPES[user_type]
      thresholds = params[direction]
      g_abs      = abs(slope_pct)
      g_opt      = thresholds["g_opt"]
      g_warn     = thresholds["g_warn"]

      if g_abs <= g_opt:
          return "optimal"
      elif g_abs <= g_warn:
          return "warning"
      else:
          return "forbidden"


  @app.route("/")
  def index():
      return send_from_directory(".", "index.html")

  KAKAO_HEADERS = lambda: {"Authorization": f"KakaoAK {KAKAO_KEY}"}

  def _kakao_keyword(q: str) -> tuple:
      # 카카오 키워드 검색 API (상호명·POI)
      params = {
          "query": q,
          "x":     KAKAO_CENTER["x"],
          "y":     KAKAO_CENTER["y"],
          "size":  15,
          "sort":  "accuracy",
      }
      try:
          r   = req.get(
              "https://dapi.kakao.com/v2/local/search/keyword.json",
              headers=KAKAO_HEADERS(),
              params=params,
              timeout=3,
          )
          body = r.json()
          print(f"[keyword] status={r.status_code}  body={body}")

          if r.status_code != 200:
              return [], {"status": r.status_code, "body": body}

          out  = []
          seen = set()
          for doc in body.get("documents", []):
              name = re.sub(r"<[^>]+>", "", doc["place_name"])
              key  = (name, doc["x"], doc["y"])
              if key in seen:
                  continue
              seen.add(key)
              out.append({
                  "name":     name,
                  "lon":      float(doc["x"]),
                  "lat":      float(doc["y"]),
                  "type":     "place",
                  "address":  doc.get("road_address_name") or doc.get("address_name", ""),
                  "category": doc.get("category_group_name", "") or doc.get("category_name", "").split(" > ")[-1],
                  "distance": doc.get("distance", ""),
                  "phone":    doc.get("phone", ""),
                  "url":      doc.get("place_url", ""),
              })
          return out, {"status": r.status_code, "body": body}
      except Exception as e:
          print(f"[카카오 keyword 예외] {e}")
          return [], {"status": -1, "body": {"exception": str(e)}}


  def _kakao_address(q: str) -> tuple:
      # 카카오 주소 검색 API (도로명·지번)
      params = {
          "query":        q,
          "analyze_type": "similar",
          "size":         5,
      }
      try:
          r    = req.get(
              "https://dapi.kakao.com/v2/local/search/address.json",
              headers=KAKAO_HEADERS(),
              params=params,
              timeout=3,
          )
          body = r.json()
          print(f"[address] status={r.status_code}  body={body}")

          if r.status_code != 200:
              return [], {"status": r.status_code, "body": body}

          out = []
          for doc in body.get("documents", []):
              ra      = doc.get("road_address") or {}
              aa      = doc.get("address")      or {}
              name    = ra.get("address_name") or aa.get("address_name", "")
              display = ra.get("address_name", "") or aa.get("address_name", "")
              if not name or not doc.get("x"):
                  continue
              out.append({
                  "name":     name,
                  "lon":      float(doc["x"]),
                  "lat":      float(doc["y"]),
                  "type":     "address",
                  "address":  display,
                  "category": "주소",
                  "distance": "",
                  "phone":    "",
                  "url":      "",
              })
          return out, {"status": r.status_code, "body": body}
      except Exception as e:
          print(f"[카카오 address 예외] {e}")
          return [], {"status": -1, "body": {"exception": str(e)}}


  # 카테고리별 우선순위 보너스
  _CATEGORY_BONUS = {
      "지하철": 12, "전철": 12, "기차": 12, "공항": 12,
      "버스":   10,
      "병원":    8, "대학교":  8, "학교":  8,
      "관공서":  6, "공공기관": 6,
      "쇼핑":    4, "마트":    4,
      "음식점":  2, "카페":    2,
  }

  def _rank_score(item: dict, query: str) -> float:
      # 이름 일치율 + 거리 근접도 + 카테고리 보너스로 검색 결과 정렬
      q = query.strip().lower()
      n = item["name"].lower()

      if n == q:
          text = 100
      elif n.startswith(q):
          text = 85
      elif q in n:
          pos  = n.index(q)
          text = max(60, 75 - pos * 2)
      else:
          text = 40

      try:
          dist_m = int(item.get("distance") or 9999)
      except ValueError:
          dist_m = 9999
      if   dist_m <=  300: dist_score = 15
      elif dist_m <= 1000: dist_score = 10
      elif dist_m <= 3000: dist_score =  5
      else:                dist_score =  0

      cat = item.get("category", "")
      cat_score = 0
      for keyword, bonus in _CATEGORY_BONUS.items():
          if keyword in cat:
              cat_score = bonus
              break

      return text + dist_score + cat_score


  @app.route("/search_places")
  def search_places():
      # 장소 자동완성: ?q=검색어 → 최대 10개 결과
      q = request.args.get("q", "").strip()
      if not q:
          return jsonify([])
      if not KAKAO_KEY:
          return jsonify([])

      def _coord_key(lon, lat):
          return (round(lon, 4), round(lat, 4))

      # 키워드 + 주소 병렬 호출 후 병합
      with ThreadPoolExecutor(max_workers=2) as ex:
          futures = {
              ex.submit(_kakao_keyword, q): "keyword",
              ex.submit(_kakao_address, q): "address",
          }
          kakao_keyword_results = []
          kakao_address_results = []
          for f in as_completed(futures):
              tag        = futures[f]
              data, _raw = f.result()
              if tag == "keyword":
                  kakao_keyword_results = data
              else:
                  kakao_address_results = data

      results     = []
      seen_coords = set()
      for item in kakao_keyword_results + kakao_address_results:
          ck = _coord_key(item["lon"], item["lat"])
          if ck not in seen_coords:
              seen_coords.add(ck)
              results.append(item)

      results.sort(key=lambda x: _rank_score(x, q), reverse=True)

      print(f"[search_places] q={q!r}  keyword={len(kakao_keyword_results)}  "
            f"address={len(kakao_address_results)}  merged={len(results)}")
      for r in results[:5]:
          print(f"  score={_rank_score(r, q):5.1f}  {r['name']}  ({r['category']})  {r.get('distance','')}m")
      return jsonify(results[:10])


  @app.route("/debug_search")
  def debug_search():
      # 개발용: 카카오 두 API 원본 응답 반환
      q = request.args.get("q", "").strip()
      if not q:
          return jsonify({"error": "q 파라미터 필요"}), 400
      if not KAKAO_KEY:
          return jsonify({"error": "KAKAO_KEY 미설정"}), 500

      with ThreadPoolExecutor(max_workers=2) as ex:
          f_kw = ex.submit(_kakao_keyword, q)
          f_ad = ex.submit(_kakao_address, q)
          kw_list, kw_info = f_kw.result()
          ad_list, ad_info = f_ad.result()

      return jsonify({
          "query": q,
          "keyword": {
              "http_status": kw_info["status"],
              "raw":         kw_info["body"],
              "parsed":      kw_list,
          },
          "address": {
              "http_status": ad_info["status"],
              "raw":         ad_info["body"],
              "parsed":      ad_list,
          },
      })


  @app.route("/route", methods=["POST"])
  def route():
      # 경로 탐색 엔드포인트 (POST JSON)
      data = request.get_json(force=True)
      try:
          origin    = (float(data["origin_lon"]), float(data["origin_lat"]))
          dest      = (float(data["dest_lon"]),   float(data["dest_lat"]))
          user_type = data.get("user_type", "elderly")
          algorithm = data.get("algorithm", "auto")
          mode      = data.get("mode", "accessibility")
          result    = find_route(G, origin, dest, user_type, algorithm, mode=mode)

          coords = []
          for node in result["path_nodes"]:
              nd = G.nodes[node]
              coords.append([nd["lon"], nd["lat"]])

          segs = []
          for s in result["segments"]:
              u_node    = s["u"]
              v_node    = s["v"]
              direction = s["direction"]
              slope_pct = s["slope_pct"]
              zone      = classify_zone(slope_pct, direction, user_type)
              segs.append({
                  "name":      s.get("name", ""),
                  "direction": direction,
                  "slope_pct": round(slope_pct, 2),
                  "length_m":  round(s["length_m"], 1),
                  "score":     round(s["score_by_type"].get(user_type, 0), 1),
                  "zone":      zone,
                  "coords": [
                      [G.nodes[u_node]["lon"], G.nodes[u_node]["lat"]],
                      [G.nodes[v_node]["lon"], G.nodes[v_node]["lat"]],
                  ],
              })

          return jsonify({
              "algorithm":      result["algorithm"],
              "total_length_m": round(result["total_length_m"], 1),
              "total_cost":     round(result["total_cost"], 2),
              "avg_slope_pct":  round(result["avg_slope_pct"], 2),
              "user_label":     result["user_label"],
              "coordinates":    coords,
              "segments":       segs,
          })

      except KeyError as e:
          return jsonify({"error": f"필수 파라미터 누락: {e}"}), 400
      except ValueError as e:
          return jsonify({"error": str(e)}), 400
      except Exception as e:
          return jsonify({"error": f"서버 오류: {e}"}), 500


  @app.route("/user_types")
  def user_types():
      return jsonify([
          {"key": k, "label": v["label"]}
          for k, v in USER_TYPES.items()
      ])


  if __name__ == "__main__":
      app.run(debug=True, port=5000)
