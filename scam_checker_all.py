import logging
import asyncio
import sqlite3
from datetime import datetime, timedelta
import requests
import json
from typing import Dict, List, Any, Tuple

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 데이터베이스에서 모든 사용자의 토큰 목록 가져오기
def get_all_tokens() -> List[Tuple[int, str, str]]:
    """
    데이터베이스에서 모든 사용자의 토큰 목록을 가져옵니다.
    
    Returns:
        List[Tuple[int, str, str]]: (user_id, token_address, network) 형태의 튜플 리스트
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, token, network FROM tokens")
    tokens = cursor.fetchall()
    conn.close()
    return tokens

# 특정 사용자의 토큰 목록 가져오기
def get_user_tokens(user_id: int) -> List[Tuple[str, str]]:
    """
    특정 사용자의 토큰 목록을 가져옵니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        List[Tuple[str, str]]: (token_address, network) 형태의 튜플 리스트
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute("SELECT token, network FROM tokens WHERE user_id = ?", (user_id,))
    tokens = cursor.fetchall()
    conn.close()
    return tokens

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

# 토큰 스캠 위험도 분석 함수 개선
async def check_token_scam(token_address: str, network: str = "ethereum") -> Dict[str, Any]:
    """
    토큰의 스캠 위험도를 분석합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str, optional): 네트워크 이름. 기본값은 "ethereum"
        
    Returns:
        Dict[str, Any]: 스캠 분석 결과
    """
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # 토큰 기본 정보 조회
        token_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"토큰 정보 API 요청: {token_url}")
        token_response = requests.get(token_url, headers=headers)
        
        if token_response.status_code != 200:
            return {
                "success": False,
                "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {token_response.status_code}"
            }
        
        token_data = token_response.json()
        
        if 'data' not in token_data or 'attributes' not in token_data['data']:
            return {
                "success": False,
                "error": "API 응답에 필요한 데이터가 없습니다."
            }
        
        token_attributes = token_data['data']['attributes']
        token_name = token_attributes.get('name', '알 수 없음')
        token_symbol = token_attributes.get('symbol', '???')
        
        # 토큰 추가 정보 조회 (token_info 엔드포인트)
        token_info_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/info"
        token_info_response = requests.get(token_info_url, headers=headers)
        
        has_social_media = False
        has_website = False
        gt_score = 0
        
        if token_info_response.status_code == 200:
            token_info_data = token_info_response.json()
            if 'data' in token_info_data and 'attributes' in token_info_data['data']:
                info_attrs = token_info_data['data']['attributes']
                
                # 소셜 미디어 및 웹사이트 확인
                has_twitter = bool(info_attrs.get('twitter_handle'))
                has_telegram = bool(info_attrs.get('telegram_handle'))
                has_discord = bool(info_attrs.get('discord_url'))
                has_website = bool(info_attrs.get('websites'))
                
                has_social_media = has_twitter or has_telegram or has_discord
                
                # GeckoTerminal 점수 (신뢰도 지표)
                gt_score = float(info_attrs.get('gt_score') or 0)
        
        # 풀 정보 조회
        pools_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/pools"
        logger.info(f"풀 정보 API 요청: {pools_url}")
        pools_response = requests.get(pools_url, headers=headers)
        
        # 분석 데이터 초기화
        analysis = {
            "liquidity": 0,
            "top_holder_percentage": 0,
            "top5_percentage": 0,
            "days_since_creation": 0,
            "contract_verified": False,
            "liquidity_locked": False,
            "ownership_renounced": False,
            "has_social_media": has_social_media,
            "has_website": has_website,
            "gt_score": gt_score
        }
        
        # 유동성 분석
        total_liquidity = 0
        pool_count = 0
        oldest_pool_date = None
        
        if pools_response.status_code == 200:
            pools_data = pools_response.json()
            
            if 'data' in pools_data and pools_data['data']:
                for pool in pools_data['data']:
                    if 'attributes' in pool:
                        attrs = pool['attributes']
                        
                        # 유동성 합산
                        if attrs.get('reserve_in_usd'):
                            total_liquidity += float(attrs.get('reserve_in_usd') or 0)
                        
                        # 풀 개수 카운트
                        pool_count += 1
                        
                        # 가장 오래된 풀 날짜 확인
                        if attrs.get('pool_created_at'):
                            pool_date = datetime.fromisoformat(attrs.get('pool_created_at').replace("Z", "+00:00"))
                            if oldest_pool_date is None or pool_date < oldest_pool_date:
                                oldest_pool_date = pool_date
                
                analysis["liquidity"] = total_liquidity
                
                # 토큰 생성 시간 추정 (가장 오래된 풀 기준)
                if oldest_pool_date:
                    days_since_creation = (datetime.now().astimezone() - oldest_pool_date).days
                    analysis["days_since_creation"] = days_since_creation
        
        # 홀더 정보 조회
        holders_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/holders"
        holders_response = requests.get(holders_url, headers=headers)
        
        if holders_response.status_code == 200:
            holders_data = holders_response.json()
            
            if 'data' in holders_data and holders_data['data']:
                # 상위 홀더 비율 계산
                if len(holders_data['data']) > 0 and 'attributes' in holders_data['data'][0]:
                    top_holder = holders_data['data'][0]['attributes']
                    if 'percentage' in top_holder:
                        analysis["top_holder_percentage"] = float(top_holder['percentage'])
                
                # 상위 5개 홀더 비율 계산
                if len(holders_data['data']) >= 5:
                    top5_percentage = 0
                    for i in range(min(5, len(holders_data['data']))):
                        if 'attributes' in holders_data['data'][i] and 'percentage' in holders_data['data'][i]['attributes']:
                            top5_percentage += float(holders_data['data'][i]['attributes']['percentage'])
                    
                    analysis["top5_percentage"] = top5_percentage
        
        # 스캠 지표 분석
        scam_indicators = []
        scam_score = 0  # 0-100, 높을수록 위험
        
        # 1. 유동성 검사
        if total_liquidity < 5000:
            scam_indicators.append(f"매우 낮은 유동성 (${total_liquidity:.2f})")
            scam_score += 30
        elif total_liquidity < 50000:
            scam_indicators.append(f"낮은 유동성 (${total_liquidity:.2f})")
            scam_score += 15
        
        # 2. 토큰 생성 시간 (추정)
        if analysis["days_since_creation"] < 7:
            scam_indicators.append(f"최근에 생성된 토큰 ({analysis['days_since_creation']}일 전)")
            scam_score += 20
        elif analysis["days_since_creation"] < 30:
            scam_indicators.append(f"비교적 새로운 토큰 ({analysis['days_since_creation']}일 전)")
            scam_score += 10
        
        # 3. 홀더 집중도
        if analysis["top_holder_percentage"] > 50:
            scam_indicators.append(f"단일 주소가 토큰의 {analysis['top_holder_percentage']:.2f}% 보유")
            scam_score += 25
        elif analysis["top_holder_percentage"] > 30:
            scam_indicators.append(f"단일 주소가 토큰의 {analysis['top_holder_percentage']:.2f}% 보유")
            scam_score += 15
        
        if analysis["top5_percentage"] > 90:
            scam_indicators.append(f"상위 5개 주소가 토큰의 {analysis['top5_percentage']:.2f}% 보유")
            scam_score += 15
        elif analysis["top5_percentage"] > 80:
            scam_indicators.append(f"상위 5개 주소가 토큰의 {analysis['top5_percentage']:.2f}% 보유")
            scam_score += 10
        
        # 4. 풀 개수
        if pool_count == 0:
            scam_indicators.append("유동성 풀이 없음")
            scam_score += 30
        elif pool_count == 1:
            scam_indicators.append("유동성 풀이 하나뿐임")
            scam_score += 10
        
        # 5. 소셜 미디어 및 웹사이트
        if not has_social_media and not has_website:
            scam_indicators.append("소셜 미디어와 웹사이트 모두 없음")
            scam_score += 20
        elif not has_social_media:
            scam_indicators.append("소셜 미디어 정보 없음")
            scam_score += 10
        elif not has_website:
            scam_indicators.append("웹사이트 정보 없음")
            scam_score += 5
        
        # 6. GeckoTerminal 점수
        if gt_score > 0:
            if gt_score < 30:
                scam_indicators.append(f"낮은 GeckoTerminal 점수 ({gt_score})")
                scam_score += 15
            elif gt_score < 50:
                scam_indicators.append(f"보통 GeckoTerminal 점수 ({gt_score})")
                scam_score += 5
        
        # 스캠 위험도 판단
        scam_risk = "낮음"
        if scam_score >= 70:
            scam_risk = "매우 높음"
        elif scam_score >= 50:
            scam_risk = "높음"
        elif scam_score >= 30:
            scam_risk = "중간"
        
        return {
            "success": True,
            "token_name": token_name,
            "token_symbol": token_symbol,
            "scam_score": scam_score,
            "scam_risk": scam_risk,
            "scam_indicators": scam_indicators,
            "analysis": analysis
        }
    
    except Exception as e:
        logger.error(f"스캠 토큰 분석 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 여러 토큰 정보를 한 번에 가져오는 함수
async def get_multiple_tokens_info(token_addresses: List[str], network: str = "ethereum") -> Dict[str, Any]:
    """
    여러 토큰의 정보를 한 번에 가져옵니다.
    
    Args:
        token_addresses (List[str]): 토큰 주소 목록
        network (str, optional): 네트워크 이름. 기본값은 "ethereum"
        
    Returns:
        Dict[str, Any]: 토큰 정보 결과
    """
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # 주소 목록을 쉼표로 구분된 문자열로 변환
        addresses_str = ','.join(token_addresses)
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/multi/{addresses_str}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"다중 토큰 정보 API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'data' in data:
                tokens_data = {}
                
                for token in data['data']:
                    if 'id' in token and 'attributes' in token:
                        token_id = token['id'].split(':')[1] if ':' in token['id'] else token['id']  # 'eth:0x...' 형식에서 주소 부분만 추출
                        attrs = token['attributes']
                        
                        tokens_data[token_id] = {
                            "success": True,
                            "name": attrs.get('name', '알 수 없음'),
                            "symbol": attrs.get('symbol', '???'),
                            "price": float(attrs.get('price_usd') or 0),
                            "address": token_id,
                            "decimals": int(attrs.get('decimals') or 0),
                            "created_at": attrs.get('pool_created_at') or attrs.get('created_at'),
                            "market_cap": float(attrs.get('market_cap_usd') or attrs.get('fdv_usd') or 0),
                            "total_supply": float(attrs.get('total_supply') or 0),
                            "total_reserve_in_usd": float(attrs.get('total_reserve_in_usd') or 0),
                            "coingecko_coin_id": attrs.get('coingecko_coin_id')
                        }
                
                return {
                    "success": True,
                    "tokens": tokens_data
                }
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다."}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"다중 토큰 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 모든 토큰의 스캠 여부 확인 (개선된 버전)
async def check_all_tokens_scam() -> Dict[str, Any]:
    """
    데이터베이스에 저장된 모든 토큰의 스캠 여부를 확인합니다.
    
    Returns:
        Dict[str, Any]: 스캠 분석 결과
    """
    try:
        # 모든 토큰 가져오기
        all_tokens = get_all_tokens()
        
        if not all_tokens:
            return {
                "success": True,
                "message": "추적 중인 토큰이 없습니다.",
                "tokens": []
            }
        
        # 네트워크별로 토큰 그룹화
        network_tokens = {}
        for user_id, token, network in all_tokens:
            if network not in network_tokens:
                network_tokens[network] = []
            
            # 중복 제거
            if token not in network_tokens[network]:
                network_tokens[network].append(token)
        
        # 결과 저장
        results = []
        high_risk_count = 0
        
        # 네트워크별로 처리
        for network, tokens in network_tokens.items():
            # 한 번에 최대 50개씩 처리 (API 제한 고려)
            for i in range(0, len(tokens), 50):
                batch_tokens = tokens[i:i+50]
                
                # 한 번의 API 호출로 여러 토큰 정보 가져오기
                tokens_info = await get_multiple_tokens_info(batch_tokens, network)
                
                if tokens_info["success"]:
                    # 각 토큰에 대해 스캠 분석 수행
                    for token_address in batch_tokens:
                        # 토큰 정보가 있는 경우에만 처리
                        if token_address in tokens_info["tokens"]:
                            token_info = tokens_info["tokens"][token_address]
                            
                            # 스캠 분석 수행
                            scam_result = await check_token_scam(token_address, network)
                            
                            if scam_result["success"]:
                                # 사용자 ID 찾기
                                users = [user_id for user_id, t, n in all_tokens if t == token_address and n == network]
                                
                                result = {
                                    "token_address": token_address,
                                    "network": network,
                                    "name": token_info["name"],
                                    "symbol": token_info["symbol"],
                                    "price": token_info["price"],
                                    "risk": scam_result["scam_risk"],
                                    "score": scam_result["scam_score"],
                                    "indicators": scam_result["scam_indicators"],
                                    "users": users
                                }
                                
                                results.append(result)
                                
                                # 높은 위험도 토큰 카운트
                                if scam_result["scam_risk"] in ["높음", "매우 높음"]:
                                    high_risk_count += 1
                        else:
                            # 토큰 정보가 없는 경우 개별 API 호출로 시도
                            scam_result = await check_token_scam(token_address, network)
                            
                            if scam_result["success"]:
                                # 사용자 ID 찾기
                                users = [user_id for user_id, t, n in all_tokens if t == token_address and n == network]
                                
                                result = {
                                    "token_address": token_address,
                                    "network": network,
                                    "name": scam_result["token_name"],
                                    "symbol": scam_result["token_symbol"],
                                    "price": 0,  # 가격 정보 없음
                                    "risk": scam_result["scam_risk"],
                                    "score": scam_result["scam_score"],
                                    "indicators": scam_result["scam_indicators"],
                                    "users": users
                                }
                                
                                results.append(result)
                                
                                # 높은 위험도 토큰 카운트
                                if scam_result["scam_risk"] in ["높음", "매우 높음"]:
                                    high_risk_count += 1
                else:
                    # 다중 토큰 API가 실패한 경우 개별 API 호출로 시도
                    for token_address in batch_tokens:
                        scam_result = await check_token_scam(token_address, network)
                        
                        if scam_result["success"]:
                            # 사용자 ID 찾기
                            users = [user_id for user_id, t, n in all_tokens if t == token_address and n == network]
                            
                            result = {
                                "token_address": token_address,
                                "network": network,
                                "name": scam_result["token_name"],
                                "symbol": scam_result["token_symbol"],
                                "price": 0,  # 가격 정보 없음
                                "risk": scam_result["scam_risk"],
                                "score": scam_result["scam_score"],
                                "indicators": scam_result["scam_indicators"],
                                "users": users
                            }
                            
                            results.append(result)
                            
                            # 높은 위험도 토큰 카운트
                            if scam_result["scam_risk"] in ["높음", "매우 높음"]:
                                high_risk_count += 1
        
        return {
            "success": True,
            "tokens": results,
            "total_count": len(results),
            "high_risk_count": high_risk_count
        }
    
    except Exception as e:
        logger.error(f"모든 토큰 스캠 체크 오류: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

# 특정 사용자의 토큰 스캠 여부 확인 (개선된 버전)
async def check_user_tokens_scam(user_id: int) -> Dict[str, Any]:
    """
    특정 사용자의 토큰 스캠 여부를 확인합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        Dict[str, Any]: 스캠 분석 결과
    """
    try:
        # 사용자의 토큰 가져오기
        user_tokens = get_user_tokens(user_id)
        
        if not user_tokens:
            return {
                "success": True,
                "message": "추적 중인 토큰이 없습니다.",
                "tokens": []
            }
        
        # 네트워크별로 토큰 그룹화
        network_tokens = {}
        for token, network in user_tokens:
            if network not in network_tokens:
                network_tokens[network] = []
            
            # 중복 제거
            if token not in network_tokens[network]:
                network_tokens[network].append(token)
        
        # 결과 저장
        results = []
        high_risk_count = 0
        
        # 네트워크별로 처리
        for network, tokens in network_tokens.items():
            # 한 번에 최대 50개씩 처리 (API 제한 고려)
            for i in range(0, len(tokens), 50):
                batch_tokens = tokens[i:i+50]
                
                # 한 번의 API 호출로 여러 토큰 정보 가져오기
                tokens_info = await get_multiple_tokens_info(batch_tokens, network)
                
                if tokens_info["success"]:
                    # 각 토큰에 대해 스캠 분석 수행
                    for token_address in batch_tokens:
                        # 토큰 정보가 있는 경우에만 처리
                        if token_address in tokens_info["tokens"]:
                            token_info = tokens_info["tokens"][token_address]
                            
                            # 스캠 분석 수행
                            scam_result = await check_token_scam(token_address, network)
                            
                            if scam_result["success"]:
                                result = {
                                    "token_address": token_address,
                                    "network": network,
                                    "name": token_info["name"],
                                    "symbol": token_info["symbol"],
                                    "price": token_info["price"],
                                    "risk": scam_result["scam_risk"],
                                    "score": scam_result["scam_score"],
                                    "indicators": scam_result["scam_indicators"],
                                    "liquidity_amount": scam_result["analysis"]["liquidity"],
                                    "top_holder_percentage": scam_result["analysis"]["top_holder_percentage"],
                                    "top5_percentage": scam_result["analysis"]["top5_percentage"],
                                    "days_since_creation": scam_result["analysis"]["days_since_creation"]
                                }
                                
                                results.append(result)
                                
                                # 높은 위험도 토큰 카운트
                                if scam_result["scam_risk"] in ["높음", "매우 높음"]:
                                    high_risk_count += 1
                        else:
                            # 토큰 정보가 없는 경우 개별 API 호출로 시도
                            scam_result = await check_token_scam(token_address, network)
                            
                            if scam_result["success"]:
                                result = {
                                    "token_address": token_address,
                                    "network": network,
                                    "name": scam_result["token_name"],
                                    "symbol": scam_result["token_symbol"],
                                    "price": 0,  # 가격 정보 없음
                                    "risk": scam_result["scam_risk"],
                                    "score": scam_result["scam_score"],
                                    "indicators": scam_result["scam_indicators"],
                                    "liquidity_amount": scam_result["analysis"]["liquidity"],
                                    "top_holder_percentage": scam_result["analysis"]["top_holder_percentage"],
                                    "top5_percentage": scam_result["analysis"]["top5_percentage"],
                                    "days_since_creation": scam_result["analysis"]["days_since_creation"]
                                }
                                
                                results.append(result)
                                
                                # 높은 위험도 토큰 카운트
                                if scam_result["scam_risk"] in ["높음", "매우 높음"]:
                                    high_risk_count += 1
                else:
                    # 다중 토큰 API가 실패한 경우 개별 API 호출로 시도
                    for token_address in batch_tokens:
                        scam_result = await check_token_scam(token_address, network)
                        
                        if scam_result["success"]:
                            result = {
                                "token_address": token_address,
                                "network": network,
                                "name": scam_result["token_name"],
                                "symbol": scam_result["token_symbol"],
                                "price": 0,  # 가격 정보 없음
                                "risk": scam_result["scam_risk"],
                                "score": scam_result["scam_score"],
                                "indicators": scam_result["scam_indicators"],
                                "liquidity_amount": scam_result["analysis"]["liquidity"],
                                "top_holder_percentage": scam_result["analysis"]["top_holder_percentage"],
                                "top5_percentage": scam_result["analysis"]["top5_percentage"],
                                "days_since_creation": scam_result["analysis"]["days_since_creation"]
                            }
                            
                            results.append(result)
                            
                            # 높은 위험도 토큰 카운트
                            if scam_result["scam_risk"] in ["높음", "매우 높음"]:
                                high_risk_count += 1
        
        return {
            "success": True,
            "tokens": results,
            "total_count": len(results),
            "high_risk_count": high_risk_count
        }
    
    except Exception as e:
        logger.error(f"사용자 토큰 스캠 체크 오류: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

# 메인 함수 (테스트용)
async def main():
    # 모든 토큰 스캠 체크 테스트
    result = await check_all_tokens_scam()
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main()) 