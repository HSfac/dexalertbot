import logging
import asyncio
import sqlite3
import requests
from datetime import datetime, timedelta
import time
from typing import Dict, List, Any

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 네트워크 ID 매핑
NETWORK_MAPPING = {
    "ethereum": "eth",
    "bsc": "bsc",
    "polygon": "polygon_pos",
    "arbitrum": "arbitrum",
    "avalanche": "avax",
    "optimism": "optimism",
    "base": "base",
    "solana": "solana"
}

# 지원하는 네트워크 목록
SUPPORTED_NETWORKS = {
    "ethereum": "이더리움 (ETH)",
    "bsc": "바이낸스 스마트 체인 (BSC)",
    "polygon": "폴리곤 (MATIC)",
    "arbitrum": "아비트럼 (ARB)",
    "avalanche": "아발란체 (AVAX)",
    "optimism": "옵티미즘 (OP)",
    "base": "베이스 (BASE)",
    "solana": "솔라나 (SOL)"
}

# 스캔할 주요 네트워크 목록
SCAN_NETWORKS = [
    "solana",      # 솔라나 (현재 가장 활발)
    "avalanche",   # 아발란체
]

# 데이터베이스 초기화
def init_db():
    """
    시장 스캔 관련 데이터베이스 테이블을 초기화합니다.
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    # 잠재적 토큰 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS potential_tokens (
        token_address TEXT,
        network TEXT,
        name TEXT,
        symbol TEXT,
        market_cap REAL,
        price REAL,
        first_seen TIMESTAMP,
        last_updated TIMESTAMP,
        breakout_detected INTEGER DEFAULT 0,
        PRIMARY KEY (token_address, network)
    )
    ''')
    
    # 알림 설정 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS breakout_alerts (
        user_id INTEGER PRIMARY KEY,
        enabled INTEGER DEFAULT 0
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("시장 스캔 데이터베이스 초기화 완료")

# API 요청 사이에 지연 시간을 추가하는 함수
async def rate_limited_request(url, headers=None, params=None):
    max_retries = 5
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, params=params)
            
            # 성공적인 응답이면 바로 반환
            if response.status_code != 429:
                return response
            
            # 429 오류(Rate Limit)인 경우 지연 후 재시도
            logger.warning(f"API 요청 제한 도달 (429). {retry_delay}초 후 재시도 ({attempt+1}/{max_retries})...")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2  # 지수 백오프 적용
            
        except Exception as e:
            logger.error(f"API 요청 중 오류: {str(e)}")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2
    
    # 모든 재시도 실패 시 마지막 응답 반환
    return response

# 최근 업데이트된 토큰 목록 가져오기
async def get_recently_updated_tokens(network: str) -> List[Dict[str, Any]]:
    """
    최근에 업데이트된 토큰 목록을 가져옵니다.
    
    Args:
        network (str): 네트워크 이름
        
    Returns:
        List[Dict[str, Any]]: 토큰 목록
    """
    try:
        # API 엔드포인트 구성
        url = "https://api.geckoterminal.com/api/v2/tokens/info_recently_updated"
        headers = {"Accept": "application/json"}
        params = {"network": network}
        
        logger.info(f"{network} 네트워크의 최근 업데이트된 토큰 목록 조회 중...")
        
        # 요청 보내기
        response = await rate_limited_request(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'data' in data:
                tokens = []
                
                for token_data in data['data']:
                    if 'attributes' in token_data:
                        attrs = token_data['attributes']
                        
                        token_info = {
                            "address": attrs.get('address'),
                            "name": attrs.get('name', '알 수 없음'),
                            "symbol": attrs.get('symbol', '???'),
                            "decimals": attrs.get('decimals', 0),
                            "image_url": attrs.get('image_url'),
                            "coingecko_coin_id": attrs.get('coingecko_coin_id'),
                            "websites": attrs.get('websites', []),
                            "discord_url": attrs.get('discord_url'),
                            "telegram_handle": attrs.get('telegram_handle'),
                            "twitter_handle": attrs.get('twitter_handle'),
                            "description": attrs.get('description', ''),
                            "gt_score": attrs.get('gt_score'),
                            "network": network
                        }
                        
                        tokens.append(token_info)
                
                logger.info(f"{network} 네트워크에서 {len(tokens)}개의 토큰을 찾았습니다.")
                return tokens
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return []
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            return []
    
    except Exception as e:
        logger.error(f"최근 토큰 목록 조회 오류: {str(e)}")
        return []

# 토큰의 시가총액 조회
async def get_token_market_cap(token_address: str, network: str) -> Dict[str, Any]:
    """
    토큰의 시가총액을 조회합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        
    Returns:
        Dict[str, Any]: 시가총액 정보
    """
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"토큰 시가총액 조회: {token_address} ({network})")
        
        # 요청 보내기
        response = await rate_limited_request(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'data' in data and 'attributes' in data['data']:
                attrs = data['data']['attributes']
                
                # 시가총액 정보 추출
                market_cap = float(attrs.get('fdv_usd') or attrs.get('market_cap_usd') or 0)
                price = float(attrs.get('price_usd') or 0)
                
                return {
                    "success": True,
                    "market_cap": market_cap,
                    "price": price,
                    "name": attrs.get('name', '알 수 없음'),
                    "symbol": attrs.get('symbol', '???')
                }
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다."}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"시가총액 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 새로운 토큰 스캔 함수
async def scan_market_for_new_tokens():
    """
    주요 네트워크의 새로운 토큰을 스캔합니다.
    """
    logger.info("시장 스캔 시작...")
    
    all_tokens = []
    
    # 선별된 네트워크만 스캔
    for network in SCAN_NETWORKS:
        try:
            tokens = await get_recently_updated_tokens(network)
            all_tokens.extend(tokens)
            
            # API 요청 사이에 지연 시간 추가
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"{network} 네트워크 스캔 중 오류: {str(e)}")
    
    # 시가총액 필터링 및 저장 로직
    potential_tokens = []
    
    for token in all_tokens:
        try:
            market_cap = await get_token_market_cap(token["address"], token["network"])
            
            # 80만~100만 달러 범위의 토큰만 선택
            if 800000 <= market_cap["market_cap"] <= 1000000:
                potential_tokens.append({
                    **token,
                    "market_cap": market_cap["market_cap"],
                    "price": market_cap["price"]
                })
        
        except Exception as e:
            logger.error(f"토큰 {token['address']} 시가총액 조회 중 오류: {str(e)}")
            continue
    
    # 데이터베이스에 저장
    if potential_tokens:
        await save_potential_tokens(potential_tokens)
        logger.info(f"{len(potential_tokens)}개의 잠재적 토큰을 발견하여 저장했습니다.")
    else:
        logger.info("조건에 맞는 새로운 토큰을 찾지 못했습니다.")

# 잠재적 돌파 토큰 추적 함수
async def track_potential_breakout_tokens():
    """
    저장된 잠재적 토큰들의 시가총액을 확인하고, 1백만 달러를 돌파한 토큰을 식별합니다.
    """
    logger.info("잠재적 돌파 토큰 추적 시작...")
    
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    # 아직 돌파가 감지되지 않은 토큰 가져오기
    cursor.execute(
        "SELECT token_address, network, name, symbol FROM potential_tokens WHERE breakout_detected = 0"
    )
    potential_tokens = cursor.fetchall()
    
    if not potential_tokens:
        logger.info("추적할 잠재적 토큰이 없습니다.")
        conn.close()
        return
    
    logger.info(f"{len(potential_tokens)}개의 잠재적 토큰을 추적합니다.")
    
    breakout_tokens = []
    
    for token_address, network, name, symbol in potential_tokens:
        try:
            # 토큰의 현재 시가총액 조회
            market_cap_info = await get_token_market_cap(token_address, network)
            
            if not market_cap_info.get('success', False):
                continue
            
            market_cap = market_cap_info.get('market_cap', 0)
            price = market_cap_info.get('price', 0)
            
            # 데이터베이스 업데이트
            cursor.execute(
                """
                UPDATE potential_tokens 
                SET market_cap = ?, price = ?, last_updated = ?
                WHERE token_address = ? AND network = ?
                """,
                (market_cap, price, datetime.now(), token_address, network)
            )
            
            # 1백만 달러 돌파 확인
            if market_cap > 1000000:
                logger.info(f"돌파 토큰 발견: {name} ({symbol}), 시가총액: ${market_cap:,.2f}")
                
                # 돌파 상태 업데이트
                cursor.execute(
                    """
                    UPDATE potential_tokens 
                    SET breakout_detected = 1
                    WHERE token_address = ? AND network = ?
                    """,
                    (token_address, network)
                )
                
                breakout_tokens.append({
                    "token_address": token_address,
                    "network": network,
                    "name": name,
                    "symbol": symbol,
                    "market_cap": market_cap,
                    "price": price
                })
            
            # 토큰 간 지연 시간 추가
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"토큰 {token_address} ({network}) 추적 중 오류: {str(e)}")
    
    conn.commit()
    conn.close()
    
    # 돌파 토큰이 있으면 알림 전송
    if breakout_tokens:
        await send_breakout_alerts(breakout_tokens)
    
    logger.info("잠재적 돌파 토큰 추적 완료")

# 돌파 알림 전송 함수
async def send_breakout_alerts(breakout_tokens: List[Dict[str, Any]]):
    """
    1백만 달러 돌파 토큰에 대한 알림을 전송합니다.
    
    Args:
        breakout_tokens (List[Dict[str, Any]]): 돌파 토큰 목록
    """
    try:
        # 알림 설정이 활성화된 사용자 가져오기
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM breakout_alerts WHERE enabled = 1")
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            logger.info("알림을 받을 사용자가 없습니다.")
            return
        
        # 텔레그램 봇 모듈 가져오기
        from main import bot
        
        for token in breakout_tokens:
            # 알림 메시지 생성
            message = (
                f"🚀 새로운 1백만 달러 시가총액 돌파 토큰 발견!\n\n"
                f"{token['name']} ({token['symbol']})\n"
                f"네트워크: {SUPPORTED_NETWORKS.get(token['network'], token['network'])}\n"
                f"현재 가격: ${token['price']:.8f}\n"
                f"시가총액: ${token['market_cap']:,.2f}\n\n"
                f"GeckoTerminal에서 차트 보기:\n"
                f"https://www.geckoterminal.com/{token['network']}/tokens/{token['token_address']}\n\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # 모든 사용자에게 알림 전송
            for user_id in users:
                try:
                    await bot.send_message(user_id[0], message)
                    logger.info(f"사용자 {user_id[0]}에게 돌파 알림 전송 완료")
                except Exception as e:
                    logger.error(f"사용자 {user_id[0]}에게 알림 전송 실패: {str(e)}")
            
            # 메시지 간 지연 시간 추가
            await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"돌파 알림 전송 중 오류: {str(e)}")

# 알림 설정 활성화 함수
def enable_breakout_alerts(user_id: int) -> bool:
    """
    사용자의 돌파 알림 설정을 활성화합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        bool: 성공 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO breakout_alerts (user_id, enabled) VALUES (?, 1)",
            (user_id,)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"사용자 {user_id}의 돌파 알림 설정이 활성화되었습니다.")
        return True
    
    except Exception as e:
        logger.error(f"돌파 알림 설정 활성화 중 오류: {str(e)}")
        return False

# 알림 설정 비활성화 함수
def disable_breakout_alerts(user_id: int) -> bool:
    """
    사용자의 돌파 알림 설정을 비활성화합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        bool: 성공 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO breakout_alerts (user_id, enabled) VALUES (?, 0)",
            (user_id,)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"사용자 {user_id}의 돌파 알림 설정이 비활성화되었습니다.")
        return True
    
    except Exception as e:
        logger.error(f"돌파 알림 설정 비활성화 중 오류: {str(e)}")
        return False

# 알림 설정 상태 확인 함수
def get_breakout_alerts_status(user_id: int) -> bool:
    """
    사용자의 돌파 알림 설정 상태를 확인합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        bool: 활성화 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT enabled FROM breakout_alerts WHERE user_id = ?",
            (user_id,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return bool(result[0])
        else:
            return False
    
    except Exception as e:
        logger.error(f"돌파 알림 설정 상태 확인 중 오류: {str(e)}")
        return False

# 최근 돌파 토큰 목록 가져오기
def get_recent_breakout_tokens(limit: int = 10) -> List[Dict[str, Any]]:
    """
    최근에 1백만 달러를 돌파한 토큰 목록을 가져옵니다.
    
    Args:
        limit (int, optional): 가져올 토큰 수. 기본값은 10
        
    Returns:
        List[Dict[str, Any]]: 돌파 토큰 목록
    """
    try:
        conn = sqlite3.connect('tokens.db')
        conn.row_factory = sqlite3.Row  # 컬럼명으로 접근 가능하도록 설정
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT * FROM potential_tokens 
            WHERE breakout_detected = 1
            ORDER BY last_updated DESC
            LIMIT ?
            """,
            (limit,)
        )
        
        rows = cursor.fetchall()
        conn.close()
        
        # 딕셔너리 리스트로 변환
        breakout_tokens = []
        for row in rows:
            breakout_tokens.append(dict(row))
        
        return breakout_tokens
    
    except Exception as e:
        logger.error(f"최근 돌파 토큰 목록 가져오기 중 오류: {str(e)}")
        return []

# 잠재적 토큰 저장 함수 추가
async def save_potential_tokens(tokens: List[Dict[str, Any]]):
    """
    잠재적 토큰 목록을 데이터베이스에 저장합니다.
    
    Args:
        tokens (List[Dict[str, Any]]): 저장할 토큰 목록
    """
    if not tokens:
        return
    
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    current_time = datetime.now()
    
    for token in tokens:
        try:
            token_address = token.get('address')
            network = token.get('network')
            name = token.get('name', '알 수 없음')
            symbol = token.get('symbol', '???')
            market_cap = token.get('market_cap', 0)
            price = token.get('price', 0)
            
            # 이미 존재하는지 확인
            cursor.execute(
                "SELECT * FROM potential_tokens WHERE token_address = ? AND network = ?",
                (token_address, network)
            )
            existing_token = cursor.fetchone()
            
            if existing_token:
                # 기존 토큰 업데이트
                cursor.execute(
                    """
                    UPDATE potential_tokens 
                    SET market_cap = ?, price = ?, last_updated = ?
                    WHERE token_address = ? AND network = ?
                    """,
                    (market_cap, price, current_time, token_address, network)
                )
            else:
                # 새 토큰 추가
                cursor.execute(
                    """
                    INSERT INTO potential_tokens 
                    (token_address, network, name, symbol, market_cap, price, first_seen, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_address, 
                        network, 
                        name, 
                        symbol, 
                        market_cap, 
                        price, 
                        current_time, 
                        current_time
                    )
                )
        except Exception as e:
            logger.error(f"토큰 {token.get('address')} 저장 중 오류: {str(e)}")
    
    conn.commit()
    conn.close()
    logger.info(f"{len(tokens)}개의 잠재적 토큰을 데이터베이스에 저장했습니다.")

# 스케줄러 함수
async def market_scanner_scheduler():
    """
    시장 스캔 및 토큰 추적 스케줄러
    """
    # 데이터베이스 초기화
    init_db()
    
    while True:
        try:
            # 3시간마다 시장 스캔
            await scan_market_for_new_tokens()
            
            # 30분마다 잠재적 돌파 토큰 추적 (6번 반복)
            for _ in range(6):
                await track_potential_breakout_tokens()
                await asyncio.sleep(30 * 60)  # 30분 대기
            
        except Exception as e:
            logger.error(f"스케줄러 실행 중 오류: {str(e)}")
            await asyncio.sleep(5 * 60)  # 오류 발생 시 5분 대기 후 재시도

# 메인 함수 (테스트용)
async def main():
    # 데이터베이스 초기화
    init_db()
    
    # 시장 스캔 테스트
    await scan_market_for_new_tokens()
    
    # 잠재적 돌파 토큰 추적 테스트
    await track_potential_breakout_tokens()

if __name__ == "__main__":
    asyncio.run(main()) 