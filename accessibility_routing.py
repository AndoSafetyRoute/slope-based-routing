  # accessibility_routing.py
  # 용산구 보행 접근성 경사도 비용함수 + NetworkX 길찾기

  import json
  import math
  import networkx as nx

  # 유형별 파라미터 테이블
  USER_TYPES = {
      "manual_wheelchair": {
          "label": "수동휠체어",
          "up":   {"g_opt": 5.0,  "g_warn": 8.33},
          "down": {"g_opt": 3.0,  "g_warn": 8.33},
          "r_up": 0.63, "r_down": 0.37,
      },
      "electric_wheelchair": {
          "label": "전동휠체어",
          "up":   {"g_opt": 6.0,  "g_warn": 8.33},
          "down": {"g_opt": 3.0,  "g_warn": 5.0},
          "r_up": 0.43, "r_down": 0.57,
      },
      "walker": {
          "label": "워커",
          "up":   {"g_opt": 5.0,  "g_warn": 8.0},
          "down": {"g_opt": 0.0,  "g_warn": 3.0},
          "r_up": 0.38, "r_down": 0.62,
      },
      "cane": {
          "label": "지팡이",
          "up":   {"g_opt": 8.0,  "g_warn": 12.0},
          "down": {"g_opt": 8.0,  "g_warn": 12.0},
          "r_up": 0.50, "r_down": 0.50,
      },
      "leg_injury": {
          "label": "다리 부상",
          "up":   {"g_opt": 0.0,  "g_warn": 5.0},
          "down": {"g_opt": 5.0,  "g_warn": 8.0},
          "r_up": 0.38, "r_down": 0.62,
      },
      "elderly": {
          "label": "고령자",
          "up":   {"g_opt": 5.0,  "g_warn": 8.0},
          "down": {"g_opt": 5.0,  "g_warn": 8.0},
          "r_up": 0.50, "r_down": 0.50,
      },
  }

  FLAT_THRESHOLD   = 0.5   # 이 미만은 평지로 판단 (%)
  STDDEV_THRESHOLD = 2.0   # stddev 기준값: 이하면 mean, 초과면 max 사용        
  BRIDGE_GAP_M     = 50.0  # 연결 성분 bridge 연결 최대 거리(m)


  def slope_score(g_abs: float, direction: str, user_type: str) -> float:       
      """경사도(절댓값 %)와 방향(up/down)을 받아 0~100 점수 반환"""
      params = USER_TYPES[user_type]
      thresholds = params[direction]
      g_opt  = thresholds["g_opt"]
      g_warn = thresholds["g_warn"]
      r      = params[f"r_{direction}"]

      S_opt = 100.0 * (1.0 - r)

      if g_opt == 0.0:
          if g_abs == 0.0:
              return 100.0
          if g_abs <= g_warn:
              b = S_opt / (g_warn ** 2) if g_warn > 0 else 0
              score = 100.0 - b * (g_abs ** 2)
              return max(score, 0.0)
          else:
              return 0.0

      if g_abs <= g_opt:
          a = (100.0 * r) / (g_opt ** 2)
          score = 100.0 - a * (g_abs ** 2)
          return max(score, 0.0)
      elif g_abs <= g_warn:
          b = S_opt / ((g_warn - g_opt) ** 2) if g_warn != g_opt else
  float('inf')
          score = S_opt - b * ((g_abs - g_opt) ** 2)
          return max(score, 0.0)
      else:
          return 0.0


  def slope_cost(g_percent: float, user_type: str, edge_length_m: float = 1.0)  
  -> float:
      """부호 있는 경사도(%)를 edge 비용으로 변환"""
      direction = "up" if g_percent >= 0 else "down"
      g_abs = abs(g_percent)
      score = slope_score(g_abs, direction, user_type)

      if score == 0.0:
          return 1e6 * edge_length_m

      penalty_ratio = (100.0 - score) / 100.0
      return edge_length_m * (1.0 + penalty_ratio * 5.0)


  def haversine(lon1, lat1, lon2, lat2) -> float:
      """두 위경도 간 거리(m) 계산"""
      R = 6371000
      phi1, phi2 = math.radians(lat1), math.radians(lat2)
      dphi = math.radians(lat2 - lat1)
      dlam = math.radians(lon2 - lon1)
      a = math.sin(dphi/2)**2 +
  math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
      return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


  def determine_slope_sign(slope_abs: float, elev_start: float, elev_end: float)
   -> float:
      """이웃하는 elevation 차이로 오르막/내리막 판단"""
      diff = elev_end - elev_start
      if abs(diff) < FLAT_THRESHOLD:
          return 0.0
      return slope_abs if diff > 0 else -slope_abs


  def get_representative_slope(props: dict) -> float:
      """stddev 기준에 따라 slope1_mean 또는 slope1_max 반환"""
      stddev = props.get("slope1_stddev") or 0.0
      mean   = props.get("slope1_mean") or 0.0
      maxv   = props.get("slope1_max") or 0.0

      if stddev <= STDDEV_THRESHOLD:
          return abs(mean)
      else:
          return abs(maxv)


  def connect_components(G: nx.DiGraph, bridge_gap_m: float = BRIDGE_GAP_M) ->  
  nx.DiGraph:
      """분절된 연결 성분을 bridge_gap_m 이내 최근접 노드 쌍으로 가상 엣지를    
  추가해 연결"""
      components = sorted(nx.weakly_connected_components(G), key=len,
  reverse=True)
      n_comp = len(components)

      if n_comp == 1:
          return G

      main_nodes = list(components[0])

      for i in range(1, n_comp):
          small_comp = list(components[i])
          best_dist  = float("inf")
          best_u, best_v = None, None

          # 전역 최근접 쌍 탐색 (early exit 없이 전체 탐색)
          for u in small_comp:
              ud = G.nodes[u]
              for v in main_nodes:
                  vd = G.nodes[v]
                  d = haversine(ud["lon"], ud["lat"], vd["lon"], vd["lat"])     
                  if d < best_dist:
                      best_dist = d
                      best_u, best_v = u, v

          if best_dist <= bridge_gap_m:
              e_u = G.nodes[best_u].get("elevation", 0.0)
              e_v = G.nodes[best_v].get("elevation", 0.0)
              bridge_data = {
                  "length_m": best_dist, "slope_pct": 0.0, "slope_abs": 0.0,    
                  "elevation_start": e_u, "elevation_end": e_v,
                  "highway": "bridge", "name": ""
              }
              G.add_edge(best_u, best_v, **bridge_data)

              rev = dict(bridge_data, elevation_start=e_v, elevation_end=e_u)   
              G.add_edge(best_v, best_u, **rev)
              main_nodes.extend(small_comp)

      lcc_nodes = max(nx.weakly_connected_components(G), key=len)
      return G.subgraph(lcc_nodes).copy()


  def build_graph(geojson_path: str) -> nx.DiGraph:
      """GeoJSON 데이터를 기반으로 방향 그래프(DiGraph) 구성"""
      with open(geojson_path, encoding="utf-8") as f:
          data = json.load(f)

      G = nx.DiGraph()

      def coord_to_node(lon, lat):
          return (round(lon, 6), round(lat, 6))

      for feat in data["features"]:
          props = feat["properties"]
          geom  = feat["geometry"]

          elev_mean = props.get("elevation1_mean") or 0.0
          slope_abs = get_representative_slope(props)
          highway = props.get("highway") or ""
          name    = props.get("name") or props.get("name:ko") or ""

          # LineString과 MultiLineString 모두 처리
          geom_type = geom.get("type", "")
          if geom_type == "LineString":
              all_lines = [geom["coordinates"]]
          elif geom_type == "MultiLineString":
              all_lines = geom["coordinates"]
          else:
              continue  # Point, Polygon 등 미지원 타입 스킵

          for line_coords in all_lines:
              if len(line_coords) < 2:
                  continue

              for i in range(len(line_coords) - 1):
                  lon1, lat1 = line_coords[i][0], line_coords[i][1]
                  lon2, lat2 = line_coords[i+1][0], line_coords[i+1][1]

                  u = coord_to_node(lon1, lat1)
                  v = coord_to_node(lon2, lat2)

                  if u == v:
                      continue

                  length_m = haversine(lon1, lat1, lon2, lat2)

                  if u not in G:
                      G.add_node(u, lon=lon1, lat=lat1, elevation=elev_mean)    
                  if v not in G:
                      G.add_node(v, lon=lon2, lat=lat2, elevation=elev_mean)    

                  slope_signed = determine_slope_sign(
                      slope_abs, G.nodes[u]["elevation"],
  G.nodes[v]["elevation"]
                  )

                  edge_data = {
                      "length_m": length_m, "slope_pct": slope_signed,
                      "slope_abs": slope_abs, "elevation_start":
  G.nodes[u]["elevation"],
                      "elevation_end": G.nodes[v]["elevation"], "highway":      
  highway, "name": name,
                  }

                  G.add_edge(u, v, **edge_data)

                  rev_data = dict(edge_data)
                  rev_data["slope_pct"] = -slope_signed
                  rev_data["elevation_start"] = edge_data["elevation_end"]      
                  rev_data["elevation_end"]   = edge_data["elevation_start"]    
                  G.add_edge(v, u, **rev_data)

      return connect_components(G, BRIDGE_GAP_M)


  def recalculate_path_slopes(G: nx.DiGraph, path_nodes: list) -> list:
      """연속 elevation 비교를 통해 경로 노드의 오르막/내리막 재판단"""
      segments = []
      elevations = [G.nodes[n].get("elevation", 0.0) for n in path_nodes]       

      for i in range(len(path_nodes) - 1):
          u = path_nodes[i]
          v = path_nodes[i + 1]

          if not G.has_edge(u, v):
              continue

          edge = G[u][v]
          elev_u = elevations[i]
          elev_v = elevations[i + 1]
          slope_abs = edge.get("slope_abs", 0.0)

          prev_elev = elevations[i - 1] if i > 0 else elev_u
          next_elev = elevations[i + 2] if i + 2 < len(elevations) else elev_v  

          if i > 0 and i < len(path_nodes) - 2:
              avg_diff = ((elev_u - prev_elev) + (elev_v - elev_u)) / 2.0       
          else:
              avg_diff = elev_v - elev_u

          if abs(avg_diff) < FLAT_THRESHOLD:
              slope_signed, direction = 0.0, "flat"
          elif avg_diff > 0:
              slope_signed, direction = slope_abs, "up"
          else:
              slope_signed, direction = -slope_abs, "down"

          seg = {
              "u": u, "v": v, "length_m": edge.get("length_m", 0.0),
              "slope_pct": slope_signed, "direction": direction, "name":        
  edge.get("name", ""),
              "score_by_type": {}
          }

          for ut in USER_TYPES:
              g = abs(slope_signed)
              d = direction if direction != "flat" else "up"
              seg["score_by_type"][ut] = slope_score(g, d, ut)

          segments.append(seg)

      return segments


  def nearest_node(G: nx.DiGraph, lon: float, lat: float):
      """위경도에서 가장 가까운 그래프 노드 반환"""
      best_node, best_dist = None, float("inf")
      for node, data in G.nodes(data=True):
          d = haversine(lon, lat, data["lon"], data["lat"])
          if d < best_dist:
              best_dist, best_node = d, node
      return best_node, best_dist


  def set_edge_weights(G: nx.DiGraph, user_type: str):
      """그래프 전체 엣지에 user_type에 맞는 weight 설정"""
      for u, v, data in G.edges(data=True):
          g_pct = data.get("slope_pct", 0.0)
          length = data.get("length_m", 1.0)
          data["weight"] = slope_cost(g_pct, user_type, length)


  def heuristic(u, v, G):
      """A* 휴리스틱: 직선 거리(m)"""
      n1, n2 = G.nodes[u], G.nodes[v]
      return haversine(n1["lon"], n1["lat"], n2["lon"], n2["lat"])


  def find_route(G: nx.DiGraph, origin_lonlat: tuple, dest_lonlat: tuple,       
                 user_type: str, algorithm: str = "auto") -> dict:
      """출발지에서 목적지까지의 최적 경로 탐색"""
      if user_type not in USER_TYPES:
          raise ValueError(f"유효하지 않은 user_type: {user_type}")

      set_edge_weights(G, user_type)
      origin_node, _ = nearest_node(G, *origin_lonlat)
      dest_node, _   = nearest_node(G, *dest_lonlat)

      if not nx.has_path(G, origin_node, dest_node):
          raise ValueError("출발지와 목적지가 서로 다른 연결 구간에 있습니다.") 

      straight_dist = haversine(*origin_lonlat, *dest_lonlat)
      if algorithm == "auto":
          algorithm = "astar" if straight_dist >= 1000 else "dijkstra"

      try:
          if algorithm == "astar":
              path_nodes = nx.astar_path(
                  G, origin_node, dest_node,
                  heuristic=lambda u, v: heuristic(u, v, G), weight="weight"    
              )
              total_cost = nx.astar_path_length(
                  G, origin_node, dest_node,
                  heuristic=lambda u, v: heuristic(u, v, G), weight="weight"    
              )
          else:
              path_nodes = nx.dijkstra_path(G, origin_node, dest_node,
  weight="weight")
              total_cost = nx.dijkstra_path_length(G, origin_node, dest_node,   
  weight="weight")
      except nx.NetworkXNoPath:
          raise ValueError("경로를 찾을 수 없습니다.")

      segments = recalculate_path_slopes(G, path_nodes)
      total_length = sum(s["length_m"] for s in segments)
      avg_slope = (
          sum(abs(s["slope_pct"]) * s["length_m"] for s in segments) /
  total_length
          if total_length > 0 else 0.0
      )

      return {
          "algorithm": algorithm, "origin_node": origin_node, "dest_node":      
  dest_node,
          "path_nodes": path_nodes, "segments": segments, "total_length_m":     
  total_length,
          "total_cost": total_cost, "avg_slope_pct": avg_slope, "user_type":    
  user_type,
          "user_label": USER_TYPES[user_type]["label"],
      }


  def print_route_summary(result: dict):
      """탐색된 경로의 요약 정보 출력"""
      print(f"\n{'='*60}\n 경로 요약 [{result['user_label']} /
  {result['algorithm'].upper()}]\n{'='*60}")
      print(f" 총 거리   : {result['total_length_m']:.1f} m")
      print(f" 총 비용   : {result['total_cost']:.2f}")
      print(f" 평균 경사 : {result['avg_slope_pct']:.2f} %")
      print(f" 구간 수   : {len(result['segments'])}\n{'-'*60}")

      up_segs   = [s for s in result["segments"] if s["direction"] == "up"]     
      down_segs = [s for s in result["segments"] if s["direction"] == "down"]   
      flat_segs = [s for s in result["segments"] if s["direction"] == "flat"]   

      print(f" 오르막 구간: {len(up_segs)} / 내리막: {len(down_segs)} / 평지:   
  {len(flat_segs)}")

      forbidden = [
          s for s in result["segments"]
          if s["score_by_type"][result["user_type"]] == 0.0 and s["direction"]  
  != "flat"
      ]
      if forbidden:
          print(f"\n ⚠️ 금지구간 포함 구간: {len(forbidden)}개")
          for s in forbidden[:5]:
              print(f"     {s['name'] or '무명'} | {s['slope_pct']:+.2f}% |     
  {s['length_m']:.1f}m")
      else:
          print(" ✅ 금지구간 없음")

      print("\n [구간별 상세 - 상위 10개]")
      print(f" {'#':>3} {'방향':^6} {'경사도':>7} {'거리':>7}  {'도로명'}\n     
  {'-'*50}")
      for i, s in enumerate(result["segments"][:10], 1):
          arrow = "↑" if s["direction"] == "up" else ("↓" if s["direction"] ==  
  "down" else "→")
          print(f" {i:>3} {arrow:^6} {s['slope_pct']:>+6.2f}%
  {s['length_m']:>6.1f}m  {s['name'] or '무명'}")
      print("=" * 60)


  if __name__ == "__main__":
      GEOJSON_PATH = "/mnt/user-data/uploads/elevationcostroad.geojson"

      print("그래프 구성 중...")
      G = build_graph(GEOJSON_PATH)

      ORIGIN = (126.9947, 37.5347)
      DEST   = (126.9985, 37.5512)

      for utype in USER_TYPES:
          try:
              result = find_route(G, origin_lonlat=ORIGIN, dest_lonlat=DEST,    
                                  user_type=utype, algorithm="auto")
              print_route_summary(result)
          except ValueError as e:
              print(f"\n[{USER_TYPES[utype]['label']}] 경로 없음: {e}")
