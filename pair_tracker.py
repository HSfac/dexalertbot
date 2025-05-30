import sqlite3
import asyncio
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

def init_pair_db():
    """페어 트래킹을 위한 데이터베이스 테이블 초기화"""
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    # 토큰 페어 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS token_pairs (
        user_id INTEGER,
        pair_name TEXT,
        token_a_address TEXT,
        token_a_symbol TEXT,
        token_b_address TEXT,
        token_b_symbol TEXT,
        network TEXT DEFAULT 'ethereum',
        alert_enabled INTEGER DEFAULT 1,
        periodic_alert_enabled INTEGER DEFAULT 0,
        change_threshold REAL DEFAULT 5.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, pair_name)
    )
    ''')
    
    # 기존 테이블에 periodic_alert_enabled 컬럼이 없다면 추가
    try:
        cursor.execute('ALTER TABLE token_pairs ADD COLUMN periodic_alert_enabled INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        # 컬럼이 이미 존재하면 무시
        pass
    
    # 페어 비율 기록 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pair_ratios (
        user_id INTEGER,
        pair_name TEXT,
        ratio REAL,
        token_a_price REAL,
        token_b_price REAL,
        change_percent REAL DEFAULT 0,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id, pair_name) REFERENCES token_pairs(user_id, pair_name)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("페어 트래킹 데이터베이스 초기화 완료")

async def get_token_info_for_pair(token_address: str, network: str = "ethereum") -> dict:
    """페어용 토큰 정보 조회 함수"""
    try:
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc", 
            "polygon": "polygon_pos",
            "base": "base",
            "solana": "solana"
        }
        
        api_network = network_mapping.get(network.lower(), network.lower())
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                attrs = data['data']['attributes']
                return {
                    "success": True,
                    "name": attrs.get('name', '알 수 없음'),
                    "symbol": attrs.get('symbol', '???'),
                    "price": float(attrs.get('price_usd') or 0),
                    "address": token_address
                }
        
        return {"success": False, "error": "토큰 정보를 찾을 수 없습니다."}
    except Exception as e:
        logger.error(f"토큰 정보 조회 오류 ({token_address}): {e}")
        return {"success": False, "error": str(e)}

async def get_token_price_for_pair(token_address: str, network: str = "ethereum") -> float:
    """페어용 토큰 가격 조회 함수"""
    try:
        info = await get_token_info_for_pair(token_address, network)
        return info["price"] if info["success"] else 0.0
    except Exception as e:
        logger.error(f"토큰 가격 조회 오류 ({token_address}): {e}")
        return 0.0

def add_token_pair(user_id: int, pair_name: str, token_a: str, token_b: str, 
                   network: str = "ethereum", threshold: float = 5.0) -> Dict:
    """새로운 토큰 페어 추가"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 기존 페어 확인
        cursor.execute(
            "SELECT pair_name FROM token_pairs WHERE user_id = ? AND pair_name = ?",
            (user_id, pair_name)
        )
        
        if cursor.fetchone():
            conn.close()
            return {"success": False, "message": f"페어 '{pair_name}'이 이미 존재합니다."}
        
        # 토큰 정보를 임시로 "TKA", "TKB"로 저장 (실제 심볼은 명령어 핸들러에서 업데이트)
        cursor.execute('''
        INSERT INTO token_pairs 
        (user_id, pair_name, token_a_address, token_a_symbol, token_b_address, token_b_symbol, network, change_threshold)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, pair_name, token_a, "TKA", token_b, "TKB", network, threshold))
        
        conn.commit()
        conn.close()
        
        logger.info(f"사용자 {user_id}의 페어 '{pair_name}' 추가 완료")
        return {"success": True, "message": f"페어 '{pair_name}' 추가 완료! (변화율 임계값: {threshold}%)"}
        
    except Exception as e:
        logger.error(f"페어 추가 오류: {e}")
        return {"success": False, "message": f"페어 추가 중 오류 발생: {str(e)}"}

def update_pair_symbols(user_id: int, pair_name: str, symbol_a: str, symbol_b: str):
    """페어의 토큰 심볼 업데이트"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE token_pairs SET token_a_symbol = ?, token_b_symbol = ? WHERE user_id = ? AND pair_name = ?",
            (symbol_a, symbol_b, user_id, pair_name)
        )
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"페어 심볼 업데이트 오류: {e}")

def remove_token_pair(user_id: int, pair_name: str) -> Dict:
    """토큰 페어 제거"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 페어 존재 확인
        cursor.execute(
            "SELECT pair_name FROM token_pairs WHERE user_id = ? AND pair_name = ?",
            (user_id, pair_name)
        )
        
        if not cursor.fetchone():
            conn.close()
            return {"success": False, "message": f"페어 '{pair_name}'을 찾을 수 없습니다."}
        
        # 페어 및 관련 기록 삭제
        cursor.execute("DELETE FROM pair_ratios WHERE user_id = ? AND pair_name = ?", (user_id, pair_name))
        cursor.execute("DELETE FROM token_pairs WHERE user_id = ? AND pair_name = ?", (user_id, pair_name))
        
        conn.commit()
        conn.close()
        
        logger.info(f"사용자 {user_id}의 페어 '{pair_name}' 제거 완료")
        return {"success": True, "message": f"페어 '{pair_name}' 제거 완료!"}
        
    except Exception as e:
        logger.error(f"페어 제거 오류: {e}")
        return {"success": False, "message": f"페어 제거 중 오류 발생: {str(e)}"}

def get_user_pairs(user_id: int) -> List[Dict]:
    """사용자의 페어 목록 조회"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # periodic_alert_enabled 컬럼이 있는지 확인하고 쿼리 실행
        try:
            cursor.execute('''
            SELECT pair_name, token_a_address, token_a_symbol, token_b_address, token_b_symbol, 
                   network, alert_enabled, periodic_alert_enabled, change_threshold, created_at
            FROM token_pairs 
            WHERE user_id = ?
            ORDER BY created_at DESC
            ''', (user_id,))
        except sqlite3.OperationalError:
            # periodic_alert_enabled 컬럼이 없는 경우 기존 쿼리 사용
            cursor.execute('''
            SELECT pair_name, token_a_address, token_a_symbol, token_b_address, token_b_symbol, 
                   network, alert_enabled, change_threshold, created_at
            FROM token_pairs 
            WHERE user_id = ?
            ORDER BY created_at DESC
            ''', (user_id,))
        
        pairs = []
        for row in cursor.fetchall():
            if len(row) >= 10:  # periodic_alert_enabled 컬럼이 있는 경우
                pairs.append({
                    "pair_name": row[0],
                    "token_a_address": row[1],
                    "token_a_symbol": row[2],
                    "token_b_address": row[3],
                    "token_b_symbol": row[4],
                    "network": row[5],
                    "alert_enabled": bool(row[6]),
                    "periodic_alert_enabled": bool(row[7]),
                    "change_threshold": row[8],
                    "created_at": row[9]
                })
            else:  # 기존 스키마
                pairs.append({
                    "pair_name": row[0],
                    "token_a_address": row[1],
                    "token_a_symbol": row[2],
                    "token_b_address": row[3],
                    "token_b_symbol": row[4],
                    "network": row[5],
                    "alert_enabled": bool(row[6]),
                    "periodic_alert_enabled": False,
                    "change_threshold": row[7],
                    "created_at": row[8]
                })
        
        conn.close()
        return pairs
        
    except Exception as e:
        logger.error(f"페어 목록 조회 오류: {e}")
        return []

def toggle_pair_alert(user_id: int, pair_name: str) -> Dict:
    """페어 알림 ON/OFF 토글"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 현재 상태 확인
        cursor.execute(
            "SELECT alert_enabled FROM token_pairs WHERE user_id = ? AND pair_name = ?",
            (user_id, pair_name)
        )
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            return {"success": False, "message": f"페어 '{pair_name}'을 찾을 수 없습니다."}
        
        current_status = bool(result[0])
        new_status = not current_status
        
        # 상태 업데이트
        cursor.execute(
            "UPDATE token_pairs SET alert_enabled = ? WHERE user_id = ? AND pair_name = ?",
            (int(new_status), user_id, pair_name)
        )
        
        conn.commit()
        conn.close()
        
        status_text = "활성화" if new_status else "비활성화"
        return {"success": True, "message": f"페어 '{pair_name}' 알림이 {status_text}되었습니다."}
        
    except Exception as e:
        logger.error(f"페어 알림 토글 오류: {e}")
        return {"success": False, "message": f"알림 설정 변경 중 오류 발생: {str(e)}"}

def toggle_periodic_alert(user_id: int, pair_name: str) -> Dict:
    """페어 주기적 알림 ON/OFF 토글"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 현재 상태 확인
        try:
            cursor.execute(
                "SELECT periodic_alert_enabled FROM token_pairs WHERE user_id = ? AND pair_name = ?",
                (user_id, pair_name)
            )
        except sqlite3.OperationalError:
            # periodic_alert_enabled 컬럼이 없는 경우
            conn.close()
            return {"success": False, "message": "주기적 알림 기능을 사용하려면 봇을 재시작해주세요."}
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            return {"success": False, "message": f"페어 '{pair_name}'을 찾을 수 없습니다."}
        
        current_status = bool(result[0]) if result[0] is not None else False
        new_status = not current_status
        
        # 상태 업데이트
        cursor.execute(
            "UPDATE token_pairs SET periodic_alert_enabled = ? WHERE user_id = ? AND pair_name = ?",
            (int(new_status), user_id, pair_name)
        )
        
        conn.commit()
        conn.close()
        
        status_text = "활성화" if new_status else "비활성화"
        return {"success": True, "message": f"페어 '{pair_name}' 주기적 알림이 {status_text}되었습니다."}
        
    except Exception as e:
        logger.error(f"주기적 알림 토글 오류: {e}")
        return {"success": False, "message": f"주기적 알림 설정 변경 중 오류 발생: {str(e)}"}

async def calculate_pair_ratio(user_id: int, pair_name: str, token_a_addr: str, token_b_addr: str, network: str) -> Optional[Dict]:
    """페어 비율 계산"""
    try:
        # 두 토큰의 가격을 동시에 조회
        price_a = await get_token_price_for_pair(token_a_addr, network)
        price_b = await get_token_price_for_pair(token_b_addr, network)
        
        if price_a == 0 or price_b == 0:
            logger.warning(f"가격 조회 실패: {pair_name} (A: {price_a}, B: {price_b})")
            return None
        
        ratio = price_a / price_b
        
        # 이전 비율과 비교
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT ratio FROM pair_ratios 
        WHERE user_id = ? AND pair_name = ? 
        ORDER BY timestamp DESC LIMIT 1
        ''', (user_id, pair_name))
        
        prev_result = cursor.fetchone()
        prev_ratio = prev_result[0] if prev_result else ratio
        
        # 변화율 계산
        change_percent = ((ratio - prev_ratio) / prev_ratio) * 100 if prev_ratio != 0 else 0
        
        # 새 비율 기록
        cursor.execute('''
        INSERT INTO pair_ratios (user_id, pair_name, ratio, token_a_price, token_b_price, change_percent)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, pair_name, ratio, price_a, price_b, change_percent))
        
        conn.commit()
        conn.close()
        
        return {
            "ratio": ratio,
            "price_a": price_a,
            "price_b": price_b,
            "change_percent": change_percent,
            "prev_ratio": prev_ratio
        }
        
    except Exception as e:
        logger.error(f"페어 비율 계산 오류 ({pair_name}): {e}")
        return None

async def check_pair_alerts(bot) -> None:
    """페어 알림 확인 및 전송"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 알림이 활성화된 모든 페어 조회
        cursor.execute('''
        SELECT user_id, pair_name, token_a_address, token_a_symbol, 
               token_b_address, token_b_symbol, network, change_threshold
        FROM token_pairs 
        WHERE alert_enabled = 1
        ''')
        
        pairs = cursor.fetchall()
        conn.close()
        
        for pair in pairs:
            user_id, pair_name, token_a_addr, token_a_symbol, token_b_addr, token_b_symbol, network, threshold = pair
            
            # 비율 계산
            ratio_data = await calculate_pair_ratio(user_id, pair_name, token_a_addr, token_b_addr, network)
            
            if ratio_data and abs(ratio_data['change_percent']) >= threshold:
                # 알림 전송
                change_emoji = "📈" if ratio_data['change_percent'] > 0 else "📉"
                message = f"""
{change_emoji} **페어 비율 변화 알림**

**페어**: {pair_name}
**현재 비율**: {ratio_data['ratio']:.6f}
**변화율**: {ratio_data['change_percent']:+.2f}%

**토큰 A**: ${ratio_data['price_a']:.6f}
**토큰 B**: ${ratio_data['price_b']:.6f}

**이전 비율**: {ratio_data['prev_ratio']:.6f}
"""
                
                try:
                    await bot.send_message(user_id, message, parse_mode='Markdown')
                    logger.info(f"페어 알림 전송 완료: {user_id} - {pair_name}")
                except Exception as e:
                    logger.error(f"페어 알림 전송 실패: {user_id} - {e}")
                
    except Exception as e:
        logger.error(f"페어 알림 확인 중 오류: {e}")

async def pair_tracker_scheduler(bot) -> None:
    """페어 트래커 스케줄러 (1분마다 실행)"""
    logger.info("페어 트래커 스케줄러 시작")
    
    while True:
        try:
            # 변화율 기반 알림 확인
            await check_pair_alerts(bot)
            
            # 주기적 상태 알림 전송
            await send_periodic_alerts(bot)
            
            await asyncio.sleep(60)  # 1분 대기
        except Exception as e:
            logger.error(f"페어 트래커 스케줄러 오류: {e}")
            await asyncio.sleep(60)

def get_pair_history(user_id: int, pair_name: str, hours: int = 24) -> List[Dict]:
    """페어 비율 기록 조회"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        since_time = datetime.now() - timedelta(hours=hours)
        
        cursor.execute('''
        SELECT ratio, token_a_price, token_b_price, change_percent, timestamp
        FROM pair_ratios
        WHERE user_id = ? AND pair_name = ? AND timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 100
        ''', (user_id, pair_name, since_time))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                "ratio": row[0],
                "token_a_price": row[1],
                "token_b_price": row[2],
                "change_percent": row[3],
                "timestamp": row[4]
            })
        
        conn.close()
        return history
        
    except Exception as e:
        logger.error(f"페어 기록 조회 오류: {e}")
        return []

async def send_periodic_alerts(bot) -> None:
    """주기적 알림 전송"""
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 주기적 알림이 활성화된 모든 페어 조회
        try:
            cursor.execute('''
            SELECT user_id, pair_name, token_a_address, token_a_symbol, 
                   token_b_address, token_b_symbol, network
            FROM token_pairs 
            WHERE periodic_alert_enabled = 1
            ''')
        except sqlite3.OperationalError:
            # periodic_alert_enabled 컬럼이 없는 경우 종료
            conn.close()
            return
        
        pairs = cursor.fetchall()
        conn.close()
        
        for pair in pairs:
            user_id, pair_name, token_a_addr, token_a_symbol, token_b_addr, token_b_symbol, network = pair
            
            # 현재 비율 계산 (기록하지 않고 현재 상태만 조회)
            try:
                price_a = await get_token_price_for_pair(token_a_addr, network)
                price_b = await get_token_price_for_pair(token_b_addr, network)
                
                if price_a > 0 and price_b > 0:
                    ratio = price_a / price_b
                    
                    # 이전 비율과 비교 (변화율 계산)
                    conn = sqlite3.connect('tokens.db')
                    cursor = conn.cursor()
                    
                    cursor.execute('''
                    SELECT ratio FROM pair_ratios 
                    WHERE user_id = ? AND pair_name = ? 
                    ORDER BY timestamp DESC LIMIT 1
                    ''', (user_id, pair_name))
                    
                    prev_result = cursor.fetchone()
                    prev_ratio = prev_result[0] if prev_result else ratio
                    change_percent = ((ratio - prev_ratio) / prev_ratio) * 100 if prev_ratio != 0 else 0
                    
                    conn.close()
                    
                    # 주기적 상태 알림 전송
                    change_emoji = "📈" if change_percent > 0 else "📉" if change_percent < 0 else "➖"
                    message = f"""
📊 **주기적 상태 알림**

**페어**: {pair_name}
**현재 비율**: {ratio:.6f} {change_emoji}
**변화율**: {change_percent:+.2f}%

**{token_a_symbol}**: ${price_a:.6f}
**{token_b_symbol}**: ${price_b:.6f}

🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                    
                    try:
                        await bot.send_message(user_id, message, parse_mode='Markdown')
                        logger.info(f"주기적 알림 전송 완료: {user_id} - {pair_name}")
                        
                        # 1초 대기로 API 요청 제한 방지
                        await asyncio.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"주기적 알림 전송 실패: {user_id} - {e}")
                        
            except Exception as e:
                logger.error(f"주기적 알림 계산 오류 ({pair_name}): {e}")
                continue
                
    except Exception as e:
        logger.error(f"주기적 알림 확인 중 오류: {e}") 