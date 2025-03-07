import os
import logging
import sqlite3
import requests
import schedule
import time
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from dotenv import load_dotenv
import csv
import io
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# 스캠 체크 및 분석 모듈 임포트 추가
from scam_checker_all import check_token_scam, check_user_tokens_scam, check_all_tokens_scam
from analyze_checker_all import analyze_token, analyze_user_tokens

# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 환경 변수
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", 300))  # 기본값 5분
PRICE_CHANGE_THRESHOLD = float(os.getenv("PRICE_CHANGE_THRESHOLD", 5.0))  # 기본값 5%

# 지원하는 네트워크 목록 - 파일 상단으로 이동
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

# 네트워크 ID 매핑 (GeckoTerminal API에서 사용하는 ID로 변환)
NETWORK_MAPPING = {
    "ethereum": "eth",
    "bsc": "bsc",
    "polygon": "polygon_pos",  # "polygon"에서 "polygon_pos"로 수정
    "arbitrum": "arbitrum",
    "avalanche": "avax",
    "optimism": "optimism",
    "base": "base",
    "solana": "solana"
}

# 사용자 상태 저장
user_data = {}

# 텔레그램 봇 초기화
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot)

# 데이터베이스 초기화
def init_db():
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tokens (
        user_id INTEGER,
        token TEXT,
        network TEXT,
        last_price REAL DEFAULT 0,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, token, network)
    )
    ''')
    conn.commit()
    conn.close()
    logger.info("데이터베이스 초기화 완료")

# GeckoTerminal API를 통한 토큰 가격 조회 (수정)
async def get_token_price(token_address, network="ethereum"):
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # 디버그 로그 추가
        logger.info(f"토큰 가격 조회: 네트워크={network} (API={api_network}), 주소={token_address}")
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"API 요청: {url}")
        response = requests.get(url, headers=headers)
        
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
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다."}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            if response.status_code == 404:
                return {
                    "success": False, 
                    "error": f"GeckoTerminal에서 이 토큰을 찾을 수 없습니다. 토큰이 최근에 생성되었거나 거래량이 적어 아직 인덱싱되지 않았을 수 있습니다. 다른 토큰 주소를 시도하거나, 토큰이 해당 네트워크({network})에 있는지 확인하세요."
                }
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"가격 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 사용자별 토큰 목록 조회
def get_user_tokens(user_id):
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute("SELECT token, network FROM tokens WHERE user_id = ?", (user_id,))
    tokens = cursor.fetchall()
    conn.close()
    return tokens

# 인기 토큰 목록
POPULAR_TOKENS = {
    "ethereum": {
        "eth": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "usdt": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "usdc": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "bnb": "0xB8c77482e45F1F44dE1745F52C74426C631bDD52",
        "link": "0x514910771af9ca656af840dff83e8264ecf986ca",
        "uni": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    },
    "bsc": {
        "bnb": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "cake": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
        "busd": "0xe9e7cea3dedca5984780bafc599bd69add087d56",
    },
    "polygon": {
        "matic": "0x0000000000000000000000000000000000001010",
        "aave": "0xd6df932a45c0f255f85145f286ea0b292b21c90b",
    }
}

# DEX 시장 목록
DEX_MARKETS = {
    "ethereum": [
        {"name": "Uniswap", "id": "uniswap_ethereum"},
        {"name": "SushiSwap", "id": "sushiswap_ethereum"},
        {"name": "Curve", "id": "curve_ethereum"}
    ],
    "bsc": [
        {"name": "PancakeSwap", "id": "pancakeswap_bsc"},
        {"name": "BiSwap", "id": "biswap_bsc"},
        {"name": "ApeSwap", "id": "apeswap_bsc"}
    ],
    "solana": [
        {"name": "Raydium", "id": "raydium_solana"},
        {"name": "Orca", "id": "orca_solana"},
        {"name": "Serum", "id": "serum_solana"}
    ],
    "polygon": [
        {"name": "QuickSwap", "id": "quickswap_polygon"},
        {"name": "SushiSwap", "id": "sushiswap_polygon"},
        {"name": "Uniswap", "id": "uniswap_polygon"}
    ],
    "arbitrum": [
        {"name": "Uniswap", "id": "uniswap_arbitrum"},
        {"name": "SushiSwap", "id": "sushiswap_arbitrum"},
        {"name": "Camelot", "id": "camelot_arbitrum"}
    ]
}

# 인기 토큰 추가 명령어
@dp.message_handler(commands=['popular'])
async def add_popular_token(message: types.Message):
    # 인라인 키보드 생성
    markup = InlineKeyboardMarkup(row_width=2)
    
    # 네트워크별 버튼 추가
    for network, tokens in POPULAR_TOKENS.items():
        network_button = InlineKeyboardButton(f"{network.capitalize()} 토큰", callback_data=f"network_{network}")
        markup.add(network_button)
    
    await message.reply("🔍 <b>인기 토큰 추가</b>\n\n네트워크를 선택하세요:", reply_markup=markup, parse_mode="HTML")

# 콜백 쿼리 핸들러 - 네트워크 선택
@dp.callback_query_handler(lambda c: c.data.startswith('network_'))
async def process_network_selection(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    user_id = callback_query.from_user.id
    network = callback_query.data.split('_')[1]
    
    # 사용자 상태 저장 - step 키 추가
    user_data[user_id] = {
        "network": network,
        "step": "waiting_for_token_address"  # 이 부분이 추가됨
    }
    
    await bot.send_message(
        user_id,
        f"<b>{SUPPORTED_NETWORKS[network]}</b> 네트워크를 선택했습니다.\n\n"
        f"추가할 토큰의 주소를 입력하세요:",
        parse_mode="HTML"
    )

# 콜백 쿼리 핸들러 - 토큰 선택
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('token_'))
async def process_token_selection(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    _, network, token_address = callback_query.data.split('_')
    
    # 로딩 메시지로 변경
    await bot.edit_message_text(
        "🔍 토큰 정보를 조회 중입니다...",
        callback_query.from_user.id,
        callback_query.message.message_id
    )
    
    # 토큰 정보 확인
    price_info = await get_token_price(token_address, network)
    
    if not price_info["success"]:
        await bot.edit_message_text(
            f"❌ <b>오류</b>: {price_info['error']}",
            callback_query.from_user.id,
            callback_query.message.message_id,
            parse_mode="HTML"
        )
        return
    
    # 데이터베이스에 토큰 추가
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO tokens (user_id, token, network, last_price, last_updated) VALUES (?, ?, ?, ?, ?)",
            (callback_query.from_user.id, token_address, network, price_info["price"], datetime.now())
        )
        conn.commit()
        
        await bot.edit_message_text(
            f"✅ <b>토큰이 추가되었습니다!</b>\n\n"
            f"<b>이름</b>: {price_info['name']} ({price_info['symbol']})\n"
            f"<b>네트워크</b>: {network}\n"
            f"<b>주소</b>: <code>{token_address}</code>\n"
            f"<b>현재 가격</b>: ${price_info['price']:.8f}\n\n"
            f"이제 이 토큰의 가격 변동을 모니터링합니다. 가격이 {PRICE_CHANGE_THRESHOLD}% 이상 변동되면 알림을 받게 됩니다.",
            callback_query.from_user.id,
            callback_query.message.message_id,
            parse_mode="HTML"
        )
        logger.info(f"사용자 {callback_query.from_user.id}가 토큰 {price_info['symbol']} ({network})을 추가함")
    except Exception as e:
        await bot.edit_message_text(
            f"❌ <b>토큰 추가 중 오류가 발생했습니다</b>: {str(e)}",
            callback_query.from_user.id,
            callback_query.message.message_id,
            parse_mode="HTML"
        )
    finally:
        conn.close()

# 콜백 쿼리 핸들러 - 네트워크 목록으로 돌아가기
@dp.callback_query_handler(lambda c: c.data == 'back_to_networks')
async def back_to_networks(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    # 네트워크 선택 메뉴로 돌아가기
    markup = InlineKeyboardMarkup(row_width=2)
    
    for network in POPULAR_TOKENS.keys():
        network_button = InlineKeyboardButton(f"{network.capitalize()} 토큰", callback_data=f"network_{network}")
        markup.add(network_button)
    
    await bot.edit_message_text(
        "🔍 <b>인기 토큰 추가</b>\n\n네트워크를 선택하세요:",
        callback_query.from_user.id,
        callback_query.message.message_id,
        reply_markup=markup,
        parse_mode="HTML"
    )

# 토큰 추가 명령 처리
@dp.message_handler(commands=['add'])
async def add_token(message: types.Message):
    args = message.get_args().split()
    
    if len(args) < 1:
        await message.reply(
            "ℹ️ <b>사용법</b>: <code>/add [토큰주소] [네트워크]</code>\n"
            "네트워크는 선택사항이며, 기본값은 ethereum입니다.\n\n"
            "<b>예시</b>:\n"
            "<code>/add 0xdac17f958d2ee523a2206206994597c13d831ec7</code> - 이더리움 USDT\n"
            "<code>/add 0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82 bsc</code> - BSC의 CAKE",
            parse_mode="HTML"
        )
        return
    
    token_address = args[0].lower()
    network = args[1].lower() if len(args) > 1 else "ethereum"
    
    # 로딩 메시지 전송
    loading_message = await message.reply("🔍 토큰 정보를 조회 중입니다...")
    
    # 토큰 정보 확인
    price_info = await get_token_price(token_address, network)
    
    if not price_info["success"]:
        await loading_message.edit_text(f"❌ <b>오류</b>: {price_info['error']}", parse_mode="HTML")
        return
    
    # 데이터베이스에 토큰 추가
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO tokens (user_id, token, network, last_price, last_updated) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, token_address, network, price_info["price"], datetime.now())
        )
        conn.commit()
        await loading_message.edit_text(
            f"✅ <b>토큰이 추가되었습니다!</b>\n\n"
            f"<b>이름</b>: {price_info['name']} ({price_info['symbol']})\n"
            f"<b>네트워크</b>: {network}\n"
            f"<b>주소</b>: <code>{token_address}</code>\n"
            f"<b>현재 가격</b>: ${price_info['price']:.8f}\n\n"
            f"이제 이 토큰의 가격 변동을 모니터링합니다. 가격이 {PRICE_CHANGE_THRESHOLD}% 이상 변동되면 알림을 받게 됩니다.",
            parse_mode="HTML"
        )
        logger.info(f"사용자 {message.from_user.id}가 토큰 {price_info['symbol']} ({network})을 추가함")
    except Exception as e:
        await loading_message.edit_text(f"❌ <b>토큰 추가 중 오류가 발생했습니다</b>: {str(e)}", parse_mode="HTML")
    finally:
        conn.close()

# 토큰 제거 명령어
@dp.message_handler(commands=['remove'])
async def remove_token(message: types.Message):
    user_id = message.from_user.id
    
    # 사용자의 토큰 목록 가져오기
    tokens = get_user_tokens(user_id)
    
    if not tokens:
        await message.reply(
            "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
            "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
            parse_mode="HTML"
        )
        return
    
    # 인라인 키보드 생성
    markup = InlineKeyboardMarkup(row_width=1)
    
    for token_address, network in tokens:
        # 토큰 정보 가져오기
        token_info = await get_token_price(token_address, network)
        
        if token_info["success"]:
            button_text = f"{token_info['name']} ({token_info['symbol']}) - {network}"
        else:
            button_text = f"{token_address[:8]}...{token_address[-6:]} - {network}"
        
        callback_data = f"remove_{network}_{token_address}"
        button = InlineKeyboardButton(button_text, callback_data=callback_data)
        markup.add(button)
    
    await message.reply(
        "🗑️ <b>제거할 토큰을 선택하세요:</b>",
        reply_markup=markup,
        parse_mode="HTML"
    )

# 토큰 제거 콜백 처리 (수정)
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('remove_'))
async def process_remove_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    user_id = callback_query.from_user.id
    data_parts = callback_query.data.split('_', 2)  # 최대 2번 분할
    
    if len(data_parts) < 3:
        await bot.send_message(
            user_id,
            "❌ <b>오류</b>: 잘못된 콜백 데이터입니다.",
            parse_mode="HTML"
        )
        return
    
    network = data_parts[1]
    token_address = data_parts[2]
    
    # 디버그 로그 추가
    logger.info(f"토큰 제거 시도: 사용자={user_id}, 네트워크={network}, 토큰={token_address}")
    
    # 데이터베이스에서 토큰 제거
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    try:
        # 먼저 토큰이 존재하는지 확인
        cursor.execute(
            "SELECT * FROM tokens WHERE user_id = ? AND token = ? AND network = ?",
            (user_id, token_address, network)
        )
        token_exists = cursor.fetchone()
        
        if token_exists:
            # 토큰 제거
            cursor.execute(
                "DELETE FROM tokens WHERE user_id = ? AND token = ? AND network = ?",
                (user_id, token_address, network)
            )
            conn.commit()
            
            await bot.edit_message_text(
                f"✅ <b>토큰이 제거되었습니다!</b>\n\n"
                f"<b>네트워크</b>: {network}\n"
                f"<b>주소</b>: <code>{token_address}</code>",
                user_id,
                callback_query.message.message_id,
                parse_mode="HTML"
            )
            logger.info(f"사용자 {user_id}가 토큰 {token_address} ({network})을 제거함")
        else:
            # 대소문자 구분 없이 다시 시도
            cursor.execute(
                "SELECT * FROM tokens WHERE user_id = ? AND LOWER(token) = LOWER(?) AND LOWER(network) = LOWER(?)",
                (user_id, token_address, network)
            )
            token_exists_case_insensitive = cursor.fetchone()
            
            if token_exists_case_insensitive:
                # 실제 저장된 값으로 제거
                stored_token = token_exists_case_insensitive[1]  # token 컬럼
                stored_network = token_exists_case_insensitive[2]  # network 컬럼
                
                cursor.execute(
                    "DELETE FROM tokens WHERE user_id = ? AND token = ? AND network = ?",
                    (user_id, stored_token, stored_network)
                )
                conn.commit()
                
                await bot.edit_message_text(
                    f"✅ <b>토큰이 제거되었습니다!</b>\n\n"
                    f"<b>네트워크</b>: {stored_network}\n"
                    f"<b>주소</b>: <code>{stored_token}</code>",
                    user_id,
                    callback_query.message.message_id,
                    parse_mode="HTML"
                )
                logger.info(f"사용자 {user_id}가 토큰 {stored_token} ({stored_network})을 제거함 (대소문자 무시)")
            else:
                await bot.edit_message_text(
                    f"❌ <b>토큰을 찾을 수 없습니다.</b>\n\n"
                    f"이미 제거되었거나 존재하지 않는 토큰입니다.\n"
                    f"네트워크: {network}\n"
                    f"주소: <code>{token_address}</code>",
                    user_id,
                    callback_query.message.message_id,
                    parse_mode="HTML"
                )
                logger.warning(f"사용자 {user_id}가 존재하지 않는 토큰 {token_address} ({network})을 제거 시도함")
    except Exception as e:
        logger.error(f"토큰 제거 중 오류: {str(e)}")
        await bot.edit_message_text(
            f"❌ <b>토큰 제거 중 오류가 발생했습니다</b>: {str(e)}",
            user_id,
            callback_query.message.message_id,
            parse_mode="HTML"
        )
    finally:
        conn.close()

# 가격 조회 명령어 (개선)
@dp.message_handler(commands=['price'])
async def get_price(message: types.Message):
    args = message.get_args().split()
    
    if not args:
        # 사용자의 모든 토큰 가격 조회
        tokens = get_user_tokens(message.from_user.id)
        
        if not tokens:
            await message.reply(
                "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
                "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
                parse_mode="HTML"
            )
            return
        
        loading_message = await message.reply("💰 토큰 정보를 조회 중입니다...", parse_mode="HTML")
        
        response = "💰 <b>추적 중인 토큰 정보</b>\n\n"
        
        for i, (token_address, network) in enumerate(tokens, 1):
            # 토큰 상세 정보 조회
            token_info = await get_token_price(token_address, network)
            
            if token_info["success"]:
                # 추가 정보 조회
                additional_info = await get_token_additional_info(token_address, network)
                
                # 가격 변동 계산
                price_change_24h = additional_info.get("price_change_24h", 0) if additional_info["success"] else 0
                change_emoji = "🚀" if price_change_24h > 0 else "📉" if price_change_24h < 0 else "➖"
                
                response += f"{i}. <b>{token_info['name']} ({token_info['symbol']})</b> {change_emoji}\n"
                response += f"   네트워크: <code>{network}</code>\n"
                response += f"   가격: <b>${token_info['price']:.8f}</b>\n"
                
                if price_change_24h != 0:
                    response += f"   24시간 변동: <b>{price_change_24h:.2f}%</b>\n"
                
                # 시가총액 정보
                if additional_info["success"] and "market_cap" in additional_info:
                    market_cap = additional_info["market_cap"]
                    if isinstance(market_cap, (int, float)) and market_cap > 0:
                        market_cap_formatted = f"${market_cap:,.0f}"
                    response += f"   시가총액: <b>{market_cap_formatted}</b>\n"
                
                # 거래량 정보
                if additional_info["success"] and "volume_24h" in additional_info:
                    volume = additional_info["volume_24h"]
                    if isinstance(volume, (int, float)) and volume > 0:
                        volume_formatted = f"${volume:,.0f}"
                    response += f"   24시간 거래량: <b>{volume_formatted}</b>\n"
                
                # 유동성 정보
                if additional_info["success"] and "liquidity" in additional_info:
                    liquidity = additional_info["liquidity"]
                    if isinstance(liquidity, (int, float)) and liquidity > 0:
                        liquidity_formatted = f"${liquidity:,.0f}"
                    response += f"   유동성: <b>{liquidity_formatted}</b>\n"
                
                # 주요 DEX 정보
                if additional_info["success"] and "top_dex" in additional_info and additional_info["top_dex"]:
                    response += f"   주요 DEX: <b>{additional_info['top_dex']}</b>\n"
                
                response += "\n"
            else:
                response += f"{i}. <code>{token_address}</code> ({network}): 정보 조회 실패\n\n"
        
        await loading_message.edit_text(response, parse_mode="HTML")
    else:
        # 특정 토큰의 가격 조회
        token_address = args[0]
        network = args[1] if len(args) > 1 else "ethereum"
        
        loading_message = await message.reply("💰 토큰 정보를 조회 중입니다...", parse_mode="HTML")
        
        # 토큰 기본 정보 조회
        token_info = await get_token_price(token_address, network)
        
        if token_info["success"]:
            # 추가 정보 조회
            additional_info = await get_token_additional_info(token_address, network)
            
            # 가격 변동 계산
            price_change_24h = additional_info.get("price_change_24h", 0) if additional_info["success"] else 0
            change_emoji = "🚀" if price_change_24h > 0 else "📉" if price_change_24h < 0 else "➖"
            
            response = f"💰 <b>{token_info['name']} ({token_info['symbol']})</b> {change_emoji}\n\n"
            response += f"네트워크: <code>{network}</code>\n"
            response += f"주소: <code>{token_address}</code>\n"
            response += f"가격: <b>${token_info['price']:.8f}</b>\n"
            
            if price_change_24h != 0:
                response += f"24시간 변동: <b>{price_change_24h:.2f}%</b>\n"
            
            # 시가총액 정보
            if additional_info["success"] and "market_cap" in additional_info:
                market_cap = additional_info["market_cap"]
                if isinstance(market_cap, (int, float)) and market_cap > 0:
                    market_cap_formatted = f"${market_cap:,.0f}"
                response += f"시가총액: <b>{market_cap_formatted}</b>\n"
            
            # 총 공급량 정보
            if additional_info["success"] and "total_supply" in additional_info:
                total_supply = additional_info["total_supply"]
                if isinstance(total_supply, (int, float)) and total_supply > 0:
                    total_supply_formatted = f"{total_supply:,.0f}"
                response += f"총 공급량: <b>{total_supply_formatted}</b>\n"
            
            # 거래량 정보
            if additional_info["success"] and "volume_24h" in additional_info:
                volume = additional_info["volume_24h"]
                if isinstance(volume, (int, float)) and volume > 0:
                    volume_formatted = f"${volume:,.0f}"
                response += f"24시간 거래량: <b>{volume_formatted}</b>\n"
            
            # 유동성 정보
            if additional_info["success"] and "liquidity" in additional_info:
                liquidity = additional_info["liquidity"]
                if isinstance(liquidity, (int, float)) and liquidity > 0:
                    liquidity_formatted = f"${liquidity:,.0f}"
                    response += f"유동성: <b>{liquidity_formatted}</b>\n"
            
            # 주요 DEX 정보
            if additional_info["success"] and "top_dex" in additional_info and additional_info["top_dex"]:
                response += f"주요 DEX: <b>{additional_info['top_dex']}</b>\n"
            
            # 홀더 정보
            if additional_info["success"] and "holders_count" in additional_info:
                holders = additional_info["holders_count"]
                if isinstance(holders, (int, float)) and holders > 0:
                    response += f"홀더 수: <b>{holders:,}</b>\n"
            
            # 링크 추가
            response += f"\n<a href='https://www.geckoterminal.com/{network}/tokens/{token_address}'>GeckoTerminal 차트 보기</a>"
            
            await loading_message.edit_text(response, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await loading_message.edit_text(
                f"❌ <b>오류</b>: {token_info['error']}\n\n"
                f"올바른 토큰 주소와 네트워크를 입력했는지 확인하세요.",
                parse_mode="HTML"
            )

# 토큰 추가 정보 조회 함수
async def get_token_additional_info(token_address, network="ethereum"):
    try:
        # 네트워크 ID 매핑
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        # 네트워크 ID 변환
        api_network = network_mapping.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"추가 정보 API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            result = {"success": True}
            
            if 'data' in data and 'attributes' in data['data']:
                attributes = data['data']['attributes']
                
                # 시가총액
                if 'fdv_usd' in attributes and attributes['fdv_usd']:
                    result["market_cap"] = float(attributes['fdv_usd'])
                
                # 총 공급량
                if 'total_supply' in attributes and attributes['total_supply']:
                    result["total_supply"] = float(attributes['total_supply'])
                
                # 가격 변동
                if 'price_change_percentage' in attributes and attributes['price_change_percentage'] and 'h24' in attributes['price_change_percentage']:
                    result["price_change_24h"] = float(attributes['price_change_percentage']['h24'])
                
                # 풀 정보 조회를 위한 추가 요청
                pools_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/pools"
                pools_response = requests.get(pools_url, headers=headers)
                
                if pools_response.status_code == 200:
                    pools_data = pools_response.json()
                    
                    if 'data' in pools_data and pools_data['data']:
                        # 총 유동성 계산
                        total_liquidity = 0
                        total_volume = 0
                        top_dex = None
                        top_liquidity = 0
                        
                        for pool in pools_data['data']:
                            if 'attributes' in pool:
                                pool_attr = pool['attributes']
                                
                                # 유동성 합산
                                if 'reserve_in_usd' in pool_attr and pool_attr['reserve_in_usd']:
                                    pool_liquidity = float(pool_attr['reserve_in_usd'])
                                    total_liquidity += pool_liquidity
                                    
                                    # 가장 큰 유동성을 가진 DEX 찾기
                                    if pool_liquidity > top_liquidity:
                                        top_liquidity = pool_liquidity
                                        if 'dex_name' in pool_attr:
                                            top_dex = pool_attr['dex_name']
                                
                                # 거래량 합산
                                if 'volume_usd' in pool_attr and 'h24' in pool_attr['volume_usd'] and pool_attr['volume_usd']['h24']:
                                    total_volume += float(pool_attr['volume_usd']['h24'])
                        
                        result["liquidity"] = total_liquidity
                        result["volume_24h"] = total_volume
                        result["top_dex"] = top_dex
            
            return result
        else:
            logger.error(f"추가 정보 API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            return {"success": False, "error": f"추가 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"추가 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 상세 정보 조회 (통합 API 호출)
async def get_token_details(token_address, network="ethereum"):
    try:
        # 기본 토큰 정보 조회
        token_info = await get_token_info(token_address, network)
        
        if not token_info["success"]:
            return token_info
        
        # 유동성 풀 정보 조회
        pools_info = await get_token_pools(token_address, network)
        
        if pools_info["success"] and pools_info["data"]:
            token_info["top_pools"] = pools_info["data"]
            
            # 총 유동성 계산
            total_liquidity = sum(pool["liquidity"] for pool in pools_info["data"])
            token_info["liquidity"] = total_liquidity
            
            # 총 거래량 계산
            total_volume = sum(pool["volume_24h"] for pool in pools_info["data"])
            token_info["volume_24h"] = total_volume
        
        # 가격 변동 정보 조회 (솔라나는 건너뛰기)
        if network.lower() != "solana":  # 솔라나는 ohlcv 엔드포인트가 지원되지 않음
            price_change = await get_token_price_change(token_address, network)
            
            if price_change["success"]:
                token_info["price_change_24h"] = price_change["change_24h"]
        
        return token_info
    
    except Exception as e:
        logger.error(f"토큰 상세 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 가격 변동 조회
async def get_token_price_change(token_address, network="ethereum"):
    try:
        # 네트워크 ID 매핑
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        # 네트워크 ID 변환
        api_network = network_mapping.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/ohlcv/day"
        headers = {"Accept": "application/json"}
        
        logger.info(f"가격 변동 API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data'] and 'ohlcv_list' in data['data']['attributes']:
                ohlcv_list = data['data']['attributes']['ohlcv_list']
                
                if len(ohlcv_list) >= 2:
                    # 최신 종가와 이전 종가 비교
                    current_close = float(ohlcv_list[-1][4])
                    previous_close = float(ohlcv_list[-2][4])
                    
                    if previous_close > 0:
                        change_percent = ((current_close - previous_close) / previous_close) * 100
                        return {
                            "success": True,
                            "change_24h": change_percent
                        }
            
            # 데이터가 충분하지 않은 경우
            return {"success": True, "change_24h": 0}
        else:
            logger.error(f"가격 변동 API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            return {"success": True, "change_24h": 0}  # 오류가 있어도 전체 프로세스는 계속 진행
    
    except Exception as e:
        logger.error(f"가격 변동 조회 오류: {str(e)}")
        return {"success": True, "change_24h": 0}  # 오류가 있어도 전체 프로세스는 계속 진행

# 토큰의 유동성 풀 조회 (오류 수정)
async def get_token_pools(token_address, network="ethereum"):
    try:
        # 네트워크 ID 매핑
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        # 네트워크 ID 변환
        api_network = network_mapping.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/pools"
        headers = {"Accept": "application/json"}
        
        logger.info(f"API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data:
                pools_data = []
                
                for pool in data['data']:
                    if 'attributes' in pool:
                        attrs = pool['attributes']
                        
                        # 안전하게 값 추출
                        try:
                            reserve_in_usd = attrs.get('reserve_in_usd')
                            liquidity = float(reserve_in_usd) if reserve_in_usd and reserve_in_usd != 'null' else 0
                        except (ValueError, TypeError):
                            liquidity = 0
                        
                        try:
                            volume_usd = attrs.get('volume_usd')
                            volume_24h = float(volume_usd) if volume_usd and volume_usd != 'null' else 0
                        except (ValueError, TypeError):
                            volume_24h = 0
                        
                        pool_info = {
                            "address": attrs.get('address', '알 수 없음'),
                            "name": attrs.get('name', '알 수 없음'),
                            "dex": attrs.get('dex', '알 수 없음'),
                            "liquidity": liquidity,
                            "volume_24h": volume_24h
                        }
                        pools_data.append(pool_info)
                
                # 유동성 기준으로 정렬
                pools_data.sort(key=lambda x: x['liquidity'], reverse=True)
                
                return {
                    "success": True,
                    "data": pools_data
                }
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            if response.status_code == 404:
                return {"success": False, "error": f"토큰을 찾을 수 없습니다. 주소가 올바른지, 네트워크가 맞는지 확인하세요."}
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"유동성 풀 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 정보 조회 (시가총액 포함)
async def get_token_info(token_address, network="ethereum"):
    try:
        # 네트워크 ID 매핑
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        # 네트워크 ID 변환
        api_network = network_mapping.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                attrs = data['data']['attributes']
                price_usd = float(attrs.get('price_usd') or 0)
                token_name = attrs.get('name', '알 수 없음')
                token_symbol = attrs.get('symbol', '???')
                
                # 시가총액 정보 추출
                market_cap = None
                if attrs.get('fdv_usd'):
                    market_cap = float(attrs.get('fdv_usd'))
                
                # 총 공급량 정보 추출
                total_supply = None
                if attrs.get('total_supply'):
                    total_supply = float(attrs.get('total_supply'))
                
                result = {
                    "price": price_usd,
                    "name": token_name,
                    "symbol": token_symbol,
                    "success": True
                }
                
                if market_cap:
                    result["market_cap"] = market_cap
                
                if total_supply:
                    result["total_supply"] = total_supply
                
                return result
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            if response.status_code == 404:
                return {"success": False, "error": f"토큰을 찾을 수 없습니다. 주소가 올바른지, 네트워크가 맞는지 확인하세요."}
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"토큰 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 목록 조회 명령 처리
@dp.message_handler(commands=['list'])
async def list_tokens(message: types.Message):
    tokens = get_user_tokens(message.from_user.id)
    
    if not tokens:
        await message.reply(
            "❌ 추적 중인 토큰이 없습니다.\n"
            "<code>/dex</code> 명령어로 토큰을 검색하고 추가하세요.",
            parse_mode="HTML"
        )
        return
    
    response = "📋 <b>추적 중인 토큰 목록</b>\n\n"
    
    for i, (token_address, network) in enumerate(tokens, 1):
        price_info = await get_token_price(token_address, network)
        
        if price_info["success"]:
            response += f"{i}. <b>{price_info['name']} ({price_info['symbol']})</b>\n"
            response += f"   네트워크: <code>{network}</code>\n"
            response += f"   주소: <code>{token_address}</code>\n\n"
        else:
            response += f"{i}. <code>{token_address}</code> (정보 조회 실패)\n"
            response += f"   네트워크: <code>{network}</code>\n\n"
    
    await message.reply(response, parse_mode="HTML")

# 도움말 및 시작 명령 처리 (업데이트)
@dp.message_handler(commands=['help', 'start'])
async def send_help(message: types.Message):
    user_name = message.from_user.first_name
    
    # 봇 로고 이모지
    bot_logo = "🔍💰"
    
    help_text = (
        f"{bot_logo} <b>DEX 토큰 모니터링 봇</b> {bot_logo}\n\n"
        f"👋 안녕하세요, <b>{user_name}</b>님!\n"
        f"암호화폐 시장을 더 스마트하게 모니터링할 수 있도록 도와드립니다.\n\n"
        
        f"🌟 <b>주요 기능</b>\n"
        f"• 실시간 토큰 가격 모니터링\n"
        f"• 가격 변동 자동 알림\n"
        f"• 토큰 스캠 위험도 분석\n"
        f"• 유동성 및 거래량 추적\n"
        f"• 종합적인 토큰 분석\n\n"
        
        f"📌 <b>명령어 가이드</b>\n\n"
        
        f"<b>🔹 토큰 추적 및 관리</b>\n"
        f"<code>/dex</code> - 네트워크 선택 후 토큰 추가\n"
        f"<code>/add [토큰주소] [네트워크]</code> - 직접 토큰 추가\n"
        f"<code>/list</code> - 추적 중인 토큰 목록 조회\n"
        f"<code>/remove</code> - 토큰 제거\n"
        f"<code>/update</code> - 토큰 정보 업데이트\n\n"
        
        f"<b>🔹 가격 정보 및 모니터링</b>\n"
        f"<code>/price</code> - 모든 토큰의 가격 정보 조회\n"
        f"<code>/price [토큰주소] [네트워크]</code> - 특정 토큰 가격 조회\n"
        f"<code>/marketcap</code> - 시가총액 정보 조회\n\n"
        
        f"<b>🔹 토큰 분석 도구</b>\n"
        f"<code>/pools [토큰주소] [네트워크]</code> - 유동성 풀 정보\n"
        f"<code>/scamcheck [토큰주소] [네트워크]</code> - 스캠 위험도 분석\n"
        f"<code>/scamcheckall</code> - 모든 토큰 스캠 위험도 분석\n"
        f"<code>/analyze [토큰주소] [네트워크]</code> - 종합적인 토큰 분석\n"
        f"<code>/analyzeall</code> - 모든 토큰 종합 분석\n\n"
        
        f"⚠️ 가격 변동이 <b>{PRICE_CHANGE_THRESHOLD}%</b> 이상일 경우 자동으로 알림이 전송됩니다.\n\n"
        
        f"🌐 <b>지원하는 네트워크</b>\n"
        f"• 이더리움 (ETH)\n"
        f"• 바이낸스 스마트 체인 (BSC)\n"
        f"• 폴리곤 (MATIC)\n"
        f"• 아비트럼 (ARB)\n"
        f"• 아발란체 (AVAX)\n"
        f"• 옵티미즘 (OP)\n"
        f"• 베이스 (BASE)\n"
        f"• 솔라나 (SOL)\n\n"
        
        f"🚀 <b>시작하기</b>\n"
        f"1️⃣ <code>/dex</code> 명령어로 토큰 추가하기\n"
        f"2️⃣ <code>/price</code>로 토큰 가격 확인하기\n"
        f"3️⃣ <code>/scamcheck</code>로 토큰 안전성 확인하기\n"
        f"4️⃣ <code>/analyze</code>로 토큰 종합 분석하기\n\n"
        
        f"💡 <b>팁</b>: <code>/analyzeall</code> 명령어를 사용하면 추적 중인 모든 토큰의 종합 분석 결과를 한 번에 확인할 수 있습니다.\n\n"
        
        f"🛡️ <b>안전한 투자를 위한 조언</b>\n"
        f"• 항상 토큰의 스캠 위험도를 확인하세요\n"
        f"• 유동성이 낮은 토큰은 주의하세요\n"
        f"• 홀더 집중도가 높은 토큰은 위험할 수 있습니다\n"
        f"• 투자는 자신의 책임 하에 진행하세요\n\n"
        
        f"🤝 <b>도움이 필요하신가요?</b>\n"
        f"언제든지 <code>/help</code> 명령어를 입력하시면 이 도움말을 다시 볼 수 있습니다."
    )
    
    # 시작 버튼 추가
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    add_token_button = types.InlineKeyboardButton("➕ 토큰 추가하기", callback_data="add_token")
    price_check_button = types.InlineKeyboardButton("💰 가격 확인", callback_data="price_check")
    scam_check_button = types.InlineKeyboardButton("🛡️ 스캠 체크", callback_data="scam_check")
    analyze_button = types.InlineKeyboardButton("📊 토큰 분석", callback_data="analyze_all")
    
    markup.add(add_token_button, price_check_button)
    markup.add(scam_check_button, analyze_button)
    
    await message.reply(help_text, parse_mode="HTML", reply_markup=markup)

# 인라인 버튼 콜백 처리
@dp.callback_query_handler(lambda c: c.data in ['add_token', 'price_check', 'scam_check', 'analyze_all'])
async def process_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    if callback_query.data == "add_token":
        # 토큰 추가 명령어 실행 - 네트워크 선택 키보드 표시
        await bot.send_message(
            callback_query.from_user.id, 
            "네트워크를 선택하세요:", 
            reply_markup=get_network_keyboard()
        )
    
    elif callback_query.data == "price_check":
        # 가격 확인 명령어 실행
        message = types.Message.to_object({
            "message_id": 0, 
            "from": callback_query.from_user.to_python(), 
            "chat": callback_query.message.chat.to_python(), 
            "date": 0, 
            "text": "/price"
        })
        await get_price(message)
    
    elif callback_query.data == "scam_check":
        # 스캠 체크 명령어 실행
        message = types.Message.to_object({
            "message_id": 0, 
            "from": callback_query.from_user.to_python(), 
            "chat": callback_query.message.chat.to_python(), 
            "date": 0, 
            "text": "/scamcheckall"
        })
        await scamcheck_all_tokens(message)
    
    elif callback_query.data == "analyze_all":
        # 토큰 분석 명령어 실행
        message = types.Message.to_object({
            "message_id": 0, 
            "from": callback_query.from_user.to_python(), 
            "chat": callback_query.message.chat.to_python(), 
            "date": 0, 
            "text": "/analyzeall"
        })
        await analyze_all_tokens_command(message)

# 가격 모니터링 및 알림 전송
async def check_price_changes():
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, token, network, last_price FROM tokens")
    tokens = cursor.fetchall()
    
    logger.info(f"가격 모니터링 시작: {len(tokens)}개 토큰 확인 중...")
    alert_count = 0
    
    for user_id, token_address, network, last_price in tokens:
        try:
            price_info = await get_token_price(token_address, network)
            
            if not price_info["success"]:
                logger.error(f"토큰 {token_address} 가격 조회 실패: {price_info['error']}")
                continue
            
            current_price = price_info["price"]
            
            # 가격 변동 계산
            if last_price > 0:
                price_change_percent = abs((current_price - last_price) / last_price * 100)
                price_change_direction = "상승" if current_price > last_price else "하락"
                
                logger.info(f"토큰 {price_info['symbol']} ({network}): {price_change_percent:.2f}% {price_change_direction}")
                
                # 가격 변동이 임계값을 초과하면 알림 전송
                if price_change_percent >= PRICE_CHANGE_THRESHOLD:
                    alert_count += 1
                    
                    # 이모지 선택 (상승 시 🚀, 하락 시 📉)
                    change_emoji = "🚀" if current_price > last_price else "📉"
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"{change_emoji} <b>가격 변동 알림!</b>\n\n"
                            f"<b>{price_info['name']} ({price_info['symbol']})</b>\n"
                            f"네트워크: <code>{network}</code>\n"
                            f"이전 가격: <b>${last_price:.8f}</b>\n"
                            f"현재 가격: <b>${current_price:.8f}</b>\n"
                            f"변동: <b>{price_change_percent:.2f}% {price_change_direction}</b>\n\n"
                            f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                            parse_mode="HTML"
                        )
                        logger.info(f"알림 전송 성공 (사용자 ID: {user_id}, 토큰: {price_info['symbol']})")
                    except Exception as e:
                        logger.error(f"알림 전송 실패 (사용자 ID: {user_id}): {str(e)}")
                        # 사용자가 봇을 차단한 경우 해당 토큰 제거 고려
                        if "bot was blocked by the user" in str(e):
                            logger.warning(f"사용자 {user_id}가 봇을 차단함. 해당 사용자의 토큰을 제거할 수 있습니다.")
                            # 여기서 사용자의 토큰을 제거하는 코드를 추가할 수 있습니다
            
            # 데이터베이스 업데이트
            cursor.execute(
                "UPDATE tokens SET last_price = ?, last_updated = ? WHERE user_id = ? AND token = ? AND network = ?",
                (current_price, datetime.now(), user_id, token_address, network)
            )
        except Exception as e:
            logger.error(f"토큰 {token_address} ({network}) 모니터링 중 오류: {str(e)}")
    
    conn.commit()
    conn.close()
    
    if alert_count > 0:
        logger.info(f"가격 모니터링 완료: {alert_count}개 알림 전송됨")
    else:
        logger.info("가격 모니터링 완료: 알림 없음")

# 주기적 가격 체크 스케줄러
async def scheduler():
    while True:
        await check_price_changes()
        await asyncio.sleep(PRICE_CHECK_INTERVAL)

# 메인 함수
async def main():
    # 데이터베이스 초기화
    init_db()
    
    # 스케줄러 시작
    asyncio.create_task(scheduler())
    
    # 봇 시작
    await dp.start_polling()

# DEX 검색 명령어 (수정)
@dp.message_handler(commands=['dex'])
async def search_dex_tokens(message: types.Message):
    # 네트워크 선택 키보드
    markup = InlineKeyboardMarkup(row_width=2)
    
    for network_id, network_name in SUPPORTED_NETWORKS.items():
        network_button = InlineKeyboardButton(
            f"{network_name}", 
            callback_data=f"add_network_{network_id}"
        )
        markup.add(network_button)
    
    await message.reply(
        "🔍 <b>토큰 추가</b>\n\n"
        "먼저 토큰이 있는 블록체인 네트워크를 선택하세요:",
        reply_markup=markup,
        parse_mode="HTML"
    )

# 네트워크 선택 후 토큰 주소 입력 요청
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('add_network_'))
async def process_network_selection_for_add(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    network = callback_query.data.split('_')[2]
    network_name = SUPPORTED_NETWORKS.get(network, network.capitalize())
    
    # 사용자 상태 저장
    user_data[callback_query.from_user.id] = {
        "network": network,
        "step": "waiting_for_token_address"
    }
    
    # 네트워크별 예시 토큰 주소
    example_tokens = {
        "ethereum": "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
        "bsc": "0xe9e7cea3dedca5984780bafc599bd69add087d56",       # BUSD
        "polygon": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",   # USDC
        "arbitrum": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
        "avalanche": "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e", # USDC
        "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"   # USDC
    }
    
    example_token = example_tokens.get(network, "0xdac17f958d2ee523a2206206994597c13d831ec7")
    
    # 네트워크별 특별 안내
    special_note = ""
    if network.lower() == "solana":
        special_note = "⚠️ <b>주의</b>: 솔라나 토큰 주소는 대소문자를 구분합니다. 정확히 입력해주세요."
    
    await bot.edit_message_text(
        f"🔍 <b>{network_name} 네트워크에 추가할 토큰 주소를 입력하세요:</b>\n\n"
        f"예시: <code>{example_token}</code>\n\n"
        f"{special_note}",
        callback_query.from_user.id,
        callback_query.message.message_id,
        parse_mode="HTML"
    )

# 토큰 주소 입력 처리 (네트워크별 처리 추가)
@dp.message_handler(lambda message: message.from_user.id in user_data and user_data[message.from_user.id].get("step") == "waiting_for_token_address")
async def process_token_address(message: types.Message):
    user_id = message.from_user.id
    raw_token_address = message.text.strip()
    network = user_data[user_id]["network"]
    
    # 네트워크별 토큰 주소 처리
    if network.lower() == "solana":
        # 솔라나는 대소문자 유지
        token_address = raw_token_address
    else:
        # EVM 체인은 소문자로 변환
        token_address = raw_token_address.lower()
    
    logger.info(f"사용자 {user_id}가 {network} 네트워크에 토큰 {token_address} 추가 시도")
    
    # 로딩 메시지 표시
    loading_message = await message.reply("🔍 토큰 정보를 조회 중입니다...", parse_mode="HTML")
    
    # 토큰 정보 확인
    price_info = await get_token_price(token_address, network)
    
    if not price_info["success"]:
        logger.error(f"토큰 정보 조회 실패: {price_info['error']}")
        await loading_message.edit_text(
            f"❌ <b>오류</b>: {price_info['error']}\n\n"
            f"올바른 토큰 주소를 입력했는지 확인하세요.\n"
            f"네트워크: <code>{network}</code>\n"
            f"주소: <code>{raw_token_address}</code>",
            parse_mode="HTML"
        )
        return
    
    # 데이터베이스에 토큰 추가
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO tokens (user_id, token, network, last_price, last_updated) VALUES (?, ?, ?, ?, ?)",
            (user_id, token_address, network, price_info["price"], datetime.now())
        )
        conn.commit()
        
        # 사용자 상태 초기화
        if user_id in user_data:
            del user_data[user_id]  # 이 줄이 들여쓰기 되어야 함
        
        await loading_message.edit_text(
            f"✅ <b>토큰이 추가되었습니다!</b>\n\n"
            f"<b>이름</b>: {price_info['name']} ({price_info['symbol']})\n"
            f"<b>네트워크</b>: {SUPPORTED_NETWORKS[network]}\n"
            f"<b>주소</b>: <code>{token_address}</code>\n"
            f"<b>현재 가격</b>: ${price_info['price']:.8f}\n\n"
            f"이제 이 토큰의 가격 변동을 모니터링합니다. 가격이 {PRICE_CHANGE_THRESHOLD}% 이상 변동되면 알림을 받게 됩니다.",
            parse_mode="HTML"
        )
        logger.info(f"사용자 {user_id}가 토큰 {price_info['symbol']} ({network})을 추가함")
    except Exception as e:
        await loading_message.edit_text(
            f"❌ <b>토큰 추가 중 오류가 발생했습니다</b>: {str(e)}",
            parse_mode="HTML"
        )
    finally:
        conn.close()

# 시가총액 조회 명령어
@dp.message_handler(commands=['marketcap'])
async def get_market_cap(message: types.Message):
    args = message.get_args().split()
    
    if not args:
        # 사용자의 토큰 목록에서 시가총액 조회
        tokens = get_user_tokens(message.from_user.id)
        
        if not tokens:
            await message.reply(
                "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
                "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
                parse_mode="HTML"
            )
            return
        
        response = "💰 <b>추적 중인 토큰 시가총액</b>\n\n"
        
        for token_address, network in tokens:
            token_info = await get_token_info(token_address, network)
            
            if token_info["success"]:
                market_cap = token_info.get("market_cap", "정보 없음")
                if isinstance(market_cap, (int, float)):
                    market_cap_formatted = f"${market_cap:,.0f}"
                else:
                    market_cap_formatted = market_cap
                
                response += f"<b>{token_info['name']} ({token_info['symbol']})</b>\n"
                response += f"네트워크: <code>{network}</code>\n"
                response += f"시가총액: <b>{market_cap_formatted}</b>\n"
                response += f"현재 가격: <b>${token_info['price']:.8f}</b>\n\n"
            else:
                response += f"<code>{token_address}</code> ({network}): 정보 조회 실패\n\n"
        
        await message.reply(response, parse_mode="HTML")
    else:
        # 특정 토큰의 시가총액 조회
        token_address = args[0]
        network = args[1] if len(args) > 1 else "ethereum"
        
        loading_message = await message.reply("💰 시가총액 정보를 조회 중입니다...", parse_mode="HTML")
        
        token_info = await get_token_info(token_address, network)
        
        if token_info["success"]:
            market_cap = token_info.get("market_cap", "정보 없음")
            if isinstance(market_cap, (int, float)):
                market_cap_formatted = f"${market_cap:,.0f}"
            else:
                market_cap_formatted = market_cap
            
            response = f"💰 <b>{token_info['name']} ({token_info['symbol']}) 시가총액</b>\n\n"
            response += f"네트워크: <code>{network}</code>\n"
            response += f"주소: <code>{token_address}</code>\n"
            response += f"시가총액: <b>{market_cap_formatted}</b>\n"
            response += f"현재 가격: <b>${token_info['price']:.8f}</b>\n"
            
            if "total_supply" in token_info:
                total_supply = token_info["total_supply"]
                if isinstance(total_supply, (int, float)):
                    total_supply_formatted = f"{total_supply:,.0f}"
                else:
                    total_supply_formatted = total_supply
                response += f"총 공급량: <b>{total_supply_formatted}</b>\n"
            
            await loading_message.edit_text(response, parse_mode="HTML")
        else:
            await loading_message.edit_text(
                f"❌ <b>오류</b>: {token_info['error']}\n\n"
                f"올바른 토큰 주소와 네트워크를 입력했는지 확인하세요.",
                parse_mode="HTML"
            )

# 유동성 풀 조회 명령어
@dp.message_handler(commands=['pools'])
async def get_liquidity_pools(message: types.Message):
    args = message.get_args().split()
    
    if not args:
        await message.reply(
            "ℹ️ <b>사용법</b>: <code>/pools [토큰주소] [네트워크]</code>\n\n"
            "예시: <code>/pools 0xdac17f958d2ee523a2206206994597c13d831ec7 ethereum</code>",
            parse_mode="HTML"
        )
        return
    
    token_address = args[0]
    network = args[1] if len(args) > 1 else "ethereum"
    
    loading_message = await message.reply("💧 유동성 풀 정보를 조회 중입니다...", parse_mode="HTML")
    
    pools = await get_token_pools(token_address, network)
    
    if pools["success"]:
        if not pools["data"]:
            await loading_message.edit_text(
                f"❌ <b>유동성 풀을 찾을 수 없습니다.</b>\n\n"
                f"토큰 주소: <code>{token_address}</code>\n"
                f"네트워크: <code>{network}</code>",
                parse_mode="HTML"
            )
            return
        
        token_info = await get_token_info(token_address, network)
        token_name = token_info["name"] if token_info["success"] else token_address
        token_symbol = token_info["symbol"] if token_info["success"] else "???"
        
        response = f"💧 <b>{token_name} ({token_symbol}) 유동성 풀</b>\n\n"
        
        for i, pool in enumerate(pools["data"][:5], 1):
            response += f"{i}. <b>{pool['name']}</b> ({pool['dex']})\n"
            response += f"   풀 주소: <code>{pool['address']}</code>\n"
            response += f"   유동성: <b>${pool['liquidity']:,.0f}</b>\n"
            response += f"   24시간 거래량: <b>${pool['volume_24h']:,.0f}</b>\n\n"
        
        if len(pools["data"]) > 5:
            response += f"... 외 {len(pools['data']) - 5}개 풀이 있습니다.\n"
        
        await loading_message.edit_text(response, parse_mode="HTML")
    else:
        await loading_message.edit_text(
            f"❌ <b>오류</b>: {pools['error']}\n\n"
            f"올바른 토큰 주소와 네트워크를 입력했는지 확인하세요.",
            parse_mode="HTML"
        )

# 스캠 체크 명령어
@dp.message_handler(commands=['scamcheck'])
async def scamcheck_token(message: types.Message):
    user_id = message.from_user.id
    
    # 사용자의 토큰 목록 가져오기
    tokens = get_user_tokens(user_id)
    
    if not tokens:
        await message.reply(
            "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
            "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
            parse_mode="HTML"
        )
        return
    
    # 토큰 선택 인라인 키보드 생성
    markup = create_token_selection_markup(tokens, "scamcheck")
    
    await message.reply(
        "🔍 <b>스캠 체크할 토큰을 선택하세요</b>",
        reply_markup=markup,
        parse_mode="HTML"
    )

# 토큰 스캠 체크 콜백 처리
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('scamcheck_'))
async def process_scamcheck_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    
    user_id = callback_query.from_user.id
    data_parts = callback_query.data.split('_', 2)  # 최대 2번 분할
    
    if len(data_parts) < 3:
        await bot.send_message(
            user_id,
            "❌ <b>오류</b>: 잘못된 콜백 데이터입니다.",
            parse_mode="HTML"
        )
        return
    
    network = data_parts[1]
    token_address = data_parts[2]
    
    # 로딩 메시지 표시
    loading_message = await bot.send_message(
        user_id,
        "🔍 <b>토큰 스캠 분석 중...</b>",
        parse_mode="HTML"
    )
    
    # 스캠 체크 실행
    scam_result = await check_token_scam(token_address, network)
    
    if not scam_result["success"]:
        await bot.edit_message_text(
            f"❌ <b>분석 실패</b>: {scam_result['error']}",
            user_id,
            loading_message.message_id,
            parse_mode="HTML"
        )
        return
    
    # 토큰 정보 조회
    token_info = await get_token_info(token_address, network)
    
    # 결과 포맷팅
    risk_level = scam_result["scam_risk"]
    risk_emoji = "🟢" if risk_level == "낮음" else "🟡" if risk_level == "중간" else "🔴"
    
    response = f"{risk_emoji} <b>{scam_result['token_name']} ({scam_result['token_symbol']}) 스캠 분석</b>\n\n"
    response += f"<b>스캠 위험도</b>: {risk_level} ({scam_result['scam_score']}/100)\n\n"
    
    # 판단 근거
    if scam_result["scam_indicators"]:
        response += f"<b>위험 지표</b>:\n"
        for i, indicator in enumerate(scam_result["scam_indicators"], 1):
                response += f"{i}. {indicator}\n"
    else:
        response += f"<b>위험 지표</b>: 검사한 모든 지표에서 위험 요소가 발견되지 않았습니다.\n"
        
    response += f"\n<b>상세 정보</b>:\n"
        
        # 유동성 정보
    liquidity = scam_result["analysis"]["liquidity"]
    response += f"• 유동성: ${liquidity:,.2f}\n"
        
        # 홀더 정보
    top_holder = scam_result["analysis"]["top_holder_percentage"]
    top5_holders = scam_result["analysis"]["top5_percentage"]
    
    if top_holder > 0:
            response += f"• 최대 홀더 비율: {top_holder:.2f}%\n"
        
    if top5_holders > 0:
        response += f"• 상위 5개 홀더 비율: {top5_holders:.2f}%\n"
    
    # 생성 일자
    days_since_creation = scam_result["analysis"]["days_since_creation"]
    if days_since_creation > 0:
        response += f"• 생성 후 경과일: {days_since_creation}일\n"
    
    # 소셜 미디어 및 웹사이트 정보
    has_social = scam_result["analysis"]["has_social_media"]
    has_website = scam_result["analysis"]["has_website"]
    
    response += f"• 소셜 미디어: {'있음' if has_social else '없음'}\n"
    response += f"• 웹사이트: {'있음' if has_website else '없음'}\n"
    
    # GeckoTerminal 점수
    gt_score = scam_result["analysis"]["gt_score"]
    if gt_score > 0:
        response += f"• GeckoTerminal 점수: {gt_score}/100\n"
    
    # 토큰 정보 추가
    if token_info["success"]:
        response += f"\n<b>토큰 정보</b>:\n"
        response += f"• 주소: <code>{token_address}</code>\n"
        response += f"• 네트워크: {network}\n"
        
        if token_info.get("price", 0) > 0:
            response += f"• 현재 가격: ${token_info['price']:.8f}\n"
        
        if token_info.get("market_cap", 0) > 0:
            response += f"• 시가총액: ${token_info['market_cap']:,.2f}\n"
        
        if token_info.get("website_url"):
            response += f"• 웹사이트: {token_info['website_url']}\n"
        
        if token_info.get("twitter_url"):
            response += f"• 트위터: {token_info['twitter_url']}\n"
        
        if token_info.get("telegram_url"):
            response += f"• 텔레그램: {token_info['telegram_url']}\n"
    
    # 안전 팁 추가
    if risk_level in ["높음", "매우 높음"]:
        response += f"\n⚠️ <b>주의사항</b>:\n"
        response += f"• 이 토큰은 스캠 위험이 높습니다. 투자에 주의하세요.\n"
        response += f"• 유동성이 낮은 토큰은 가격 조작이 쉽습니다.\n"
        response += f"• 소수의 주소가 대부분의 토큰을 보유하면 덤프 위험이 있습니다.\n"
        response += f"• 최근에 생성된 토큰은 검증되지 않았을 가능성이 높습니다.\n"
    
    await bot.edit_message_text(
        response,
        user_id,
        loading_message.message_id,
            parse_mode="HTML"
        )

# 종합 분석 명령어
@dp.message_handler(commands=['analyze'])
async def analyze_token(message: types.Message):
    args = message.get_args().split()
    
    if not args:
        await message.reply(
            "ℹ️ <b>사용법</b>: <code>/analyze [토큰주소] [네트워크]</code>\n\n"
            "예시: <code>/analyze 0xdac17f958d2ee523a2206206994597c13d831ec7 ethereum</code>",
            parse_mode="HTML"
        )
        return
    
    token_address = args[0]
    network = args[1] if len(args) > 1 else "ethereum"
    
    loading_message = await message.reply("🔍 토큰을 종합적으로 분석 중입니다...", parse_mode="HTML")
    
    analysis = await get_token_comprehensive_analysis(token_address, network)
    
    if analysis["success"]:
        # 스캠 위험도에 따른 이모지 선택
        risk_emoji = "🟢"  # 기본값
        if "scam_analysis" in analysis:
            risk = analysis["scam_analysis"]["risk"]
            risk_emoji = "🟢" if risk == "낮음" else "🟡" if risk == "중간" else "🔴"
        
        response = f"{risk_emoji} <b>{analysis['name']} ({analysis['symbol']}) 종합 분석</b>\n\n"
        
        # 기본 정보
        response += "<b>기본 정보</b>:\n"
        response += f"• 네트워크: <code>{network}</code>\n"
        response += f"• 주소: <code>{token_address}</code>\n"
        response += f"• 가격: <b>${analysis['price']:.8f}</b>\n"
        
        if "market_cap" in analysis and analysis["market_cap"]:
            response += f"• 시가총액: <b>${analysis['market_cap']:,.0f}</b>\n"
        
        if "total_supply" in analysis and analysis["total_supply"]:
            response += f"• 총 공급량: <b>{analysis['total_supply']:,.0f}</b>\n"
        
        # 유동성 정보
        if "total_liquidity" in analysis:
            response += f"• 총 유동성: <b>${analysis['total_liquidity']:,.0f}</b>\n"
        
        if "total_volume_24h" in analysis:
            response += f"• 24시간 거래량: <b>${analysis['total_volume_24h']:,.0f}</b>\n"
        
        # 홀더 정보
        response += "\n<b>홀더 정보</b>:\n"
        
        if "top_holder_percentage" in analysis:
            response += f"• 최대 홀더 비율: <b>{analysis['top_holder_percentage']:.2f}%</b>\n"
        
        if "top5_concentration" in analysis:
            response += f"• 상위 5개 홀더 비율: <b>{analysis['top5_concentration']:.2f}%</b>\n"
        
        if "top_holders" in analysis and analysis["top_holders"]:
            response += "• 주요 홀더:\n"
            for i, holder in enumerate(analysis["top_holders"][:3], 1):
                holder_type = "컨트랙트" if holder.get("is_contract") else "지갑"
                response += f"  {i}. <code>{holder['address'][:8]}...{holder['address'][-6:]}</code> - {holder['percentage']:.2f}% ({holder_type})\n"
        
        # 유동성 풀 정보
        if "pools" in analysis and analysis["pools"]:
            response += "\n<b>주요 유동성 풀</b>:\n"
            for i, pool in enumerate(analysis["pools"][:3], 1):
                response += f"  {i}. <b>{pool['name']}</b> ({pool['dex']})\n"
                response += f"     유동성: <b>${pool['liquidity']:,.0f}</b>\n"
        
        # 스캠 분석
        if "scam_analysis" in analysis:
            response += f"\n<b>스캠 분석</b>: {risk_emoji} <b>{analysis['scam_analysis']['risk']}</b> (점수: {analysis['scam_analysis']['score']}/100)\n"
            
            if analysis['scam_analysis']['indicators']:
                response += "• 위험 지표:\n"
                for i, indicator in enumerate(analysis['scam_analysis']['indicators'][:3], 1):
                    response += f"  {i}. {indicator}\n"
                
                if len(analysis['scam_analysis']['indicators']) > 3:
                    response += f"  ... 외 {len(analysis['scam_analysis']['indicators']) - 3}개 지표\n"
        
        # 소셜 미디어 링크
        response += "\n<b>링크</b>:\n"
        
        if "website_url" in analysis and analysis["website_url"]:
            response += f"• <a href='{analysis['website_url']}'>웹사이트</a>\n"
        
        if "twitter_url" in analysis and analysis["twitter_url"]:
            response += f"• <a href='{analysis['twitter_url']}'>트위터</a>\n"
        
        if "telegram_url" in analysis and analysis["telegram_url"]:
            response += f"• <a href='{analysis['telegram_url']}'>텔레그램</a>\n"
        
        response += f"• <a href='https://www.geckoterminal.com/{network}/tokens/{token_address}'>GeckoTerminal 차트</a>"
        
        await loading_message.edit_text(response, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await loading_message.edit_text(
            f"❌ <b>오류</b>: {analysis['error']}\n\n"
            f"올바른 토큰 주소와 네트워크를 입력했는지 확인하세요.",
            parse_mode="HTML"
        )

# 토큰 정보 업데이트 명령어
@dp.message_handler(commands=['update'])
async def update_tokens_info(message: types.Message):
    user_id = message.from_user.id
    tokens = get_user_tokens(user_id)
    
    if not tokens:
        await message.reply(
            "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
            "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
            parse_mode="HTML"
        )
        return
    
    loading_message = await message.reply("🔄 토큰 정보를 업데이트 중입니다...", parse_mode="HTML")
    
    updated_count = 0
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    for token_address, network in tokens:
        try:
            price_info = await get_token_price(token_address, network)
            
            if price_info["success"]:
                cursor.execute(
                    "UPDATE tokens SET last_price = ?, last_updated = ? WHERE user_id = ? AND token = ? AND network = ?",
                    (price_info["price"], datetime.now(), user_id, token_address, network)
                )
                updated_count += 1
        except Exception as e:
            logger.error(f"토큰 {token_address} 업데이트 중 오류: {str(e)}")
    
    conn.commit()
    conn.close()
    
    if updated_count > 0:
        await loading_message.edit_text(
            f"✅ <b>{updated_count}개 토큰의 정보가 업데이트되었습니다.</b>\n\n"
            f"<code>/price</code> 명령어로 최신 가격을 확인하세요.",
            parse_mode="HTML"
        )
    else:
        await loading_message.edit_text(
            "❌ <b>토큰 정보 업데이트에 실패했습니다.</b>\n\n"
            "잠시 후 다시 시도하세요.",
            parse_mode="HTML"
        )

# 토큰 일괄 분석 명령어
@dp.message_handler(commands=['analyzeall'])
async def analyze_all_tokens(message: types.Message):
    user_id = message.from_user.id
    tokens = get_user_tokens(user_id)
    
    if not tokens:
        await message.reply(
            "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
            "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
            parse_mode="HTML"
        )
        return
    
    loading_message = await message.reply("🔍 추적 중인 모든 토큰을 분석 중입니다...", parse_mode="HTML")
    
    response = "🔍 <b>추적 중인 토큰 분석 결과</b>\n\n"
    
    for i, (token_address, network) in enumerate(tokens, 1):
        try:
            # 간단한 분석 정보만 가져오기
            token_info = await get_token_info(token_address, network)
            
            if token_info["success"]:
                # 스캠 체크 (간소화된 버전)
                scam_check = await check_token_scam(token_address, network)
                risk_level = "알 수 없음"
                risk_emoji = "⚪"
                
                if scam_check["success"]:
                    risk_level = scam_check["scam_risk"]
                    risk_emoji = "🟢" if risk_level == "낮음" else "🟡" if risk_level == "중간" else "🔴"
                
                # 가격 변동 계산
                price_change = await get_token_price_change(token_address, network)
                change_text = "정보 없음"
                change_emoji = "➖"
                
                if price_change["success"] and price_change["change_24h"] != 0:
                    change = price_change["change_24h"]
                    change_text = f"{change:.2f}%"
                    change_emoji = "🚀" if change > 0 else "📉"
                
                response += f"{i}. <b>{token_info['name']} ({token_info['symbol']})</b> {risk_emoji}\n"
                response += f"   가격: <b>${token_info['price']:.8f}</b> {change_emoji} {change_text}\n"
                response += f"   네트워크: <code>{network}</code>\n"
                response += f"   스캠 위험도: <b>{risk_level}</b>\n\n"
            else:
                response += f"{i}. <code>{token_address}</code> (정보 조회 실패)\n"
                response += f"   네트워크: <code>{network}</code>\n\n"
        except Exception as e:
            response += f"{i}. <code>{token_address}</code> (분석 중 오류)\n"
            response += f"   네트워크: <code>{network}</code>\n"
            response += f"   오류: {str(e)}\n\n"
    
    await loading_message.edit_text(response, parse_mode="HTML")

# 스캠 체크 일괄 실행 명령어 (개선)
@dp.message_handler(commands=['scamcheckall'])
async def scamcheck_all_tokens(message: types.Message):
    try:
        # 로딩 메시지 표시
        loading_message = await message.reply("🔍 <b>모든 토큰의 스캠 여부를 확인 중입니다...</b>", parse_mode="HTML")
        
        # 스캠 체크 실행
        scam_results = await check_all_tokens_scam()
        
        if not scam_results["success"]:
            await loading_message.edit_text(
                f"❌ <b>오류가 발생했습니다</b>: {scam_results.get('error', '알 수 없는 오류')}",
                parse_mode="HTML"
            )
            return
        
        # 결과가 없는 경우
        if scam_results.get("total_count", 0) == 0:
            await loading_message.edit_text(
                "ℹ️ <b>추적 중인 토큰이 없습니다.</b>\n\n"
                "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
                parse_mode="HTML"
            )
            return
        
        # 결과 메시지 생성
        result_text = f"🔍 <b>토큰 스캠 분석 결과</b>\n\n"
        result_text += f"총 <b>{scam_results['total_count']}</b>개 토큰 중 <b>{scam_results['high_risk_count']}</b>개가 높은 위험도를 가지고 있습니다.\n\n"
        
        # 위험도 높은 토큰 먼저 정렬
        sorted_results = sorted(
            scam_results["tokens"],  # "results" 대신 "tokens" 사용
            key=lambda x: (
                0 if x["risk"] == "매우 높음" else
                1 if x["risk"] == "높음" else
                2 if x["risk"] == "중간" else
                3
            )
        )
        
        # 상위 10개만 표시
        for i, result in enumerate(sorted_results[:10], 1):
            risk_emoji = "🔴" if result["risk"] in ["매우 높음", "높음"] else "🟠" if result["risk"] == "중간" else "🟢"
            
            result_text += f"{i}. {risk_emoji} <b>{result['name']} ({result['symbol']})</b>\n"
            result_text += f"   네트워크: {result['network']}\n"
            result_text += f"   위험도: <b>{result['risk']}</b> (점수: {result['score']})\n"
            
            if result["indicators"]:
                result_text += f"   위험 지표: {', '.join(result['indicators'][:3])}\n"
            
            result_text += f"   추적 중인 사용자: {len(result['users'])}명\n\n"
        
        # 더 많은 결과가 있는 경우
        if len(sorted_results) > 10:
            result_text += f"...외 {len(sorted_results) - 10}개 토큰\n\n"
        
        result_text += "자세한 분석을 보려면 <code>/scamcheck</code> 명령어를 사용하세요."
        
        # 결과 메시지 전송
        await loading_message.edit_text(result_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"모든 토큰 스캠 체크 명령 처리 중 오류: {str(e)}")
        await message.reply(
            f"❌ <b>오류가 발생했습니다</b>: {str(e)}",
            parse_mode="HTML"
        )

# 모든 토큰 종합 분석 명령어
@dp.message_handler(commands=['analyzeall'])
async def analyze_all_tokens_command(message: types.Message):
    user_id = message.from_user.id
    
    # 사용자의 토큰 목록 가져오기
    tokens = get_user_tokens(user_id)
    
    if not tokens:
        await message.reply(
            "❌ <b>추적 중인 토큰이 없습니다.</b>\n\n"
            "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
            parse_mode="HTML"
        )
        return
    
    loading_message = await message.reply("🔍 추적 중인 모든 토큰을 분석 중입니다...", parse_mode="HTML")
    
    # 모듈 함수 사용하여 일괄 분석
    analysis_results = await analyze_user_tokens(user_id)
    
    if not analysis_results["success"]:
        await loading_message.edit_text(
            f"❌ <b>분석 실패</b>: {analysis_results['error']}",
            parse_mode="HTML"
        )
        return
    
    # 결과 포맷팅
    response = "🔍 <b>추적 중인 토큰 분석 결과</b>\n\n"
    
    for i, result in enumerate(analysis_results["results"], 1):
        if not result["success"]:
            response += f"{i}. <code>{result['token_address']}</code> (분석 실패)\n"
            response += f"   네트워크: <code>{result['network']}</code>\n"
            response += f"   오류: {result['error']}\n\n"
            continue
        
        # 스캠 위험도 이모지
        risk_emoji = "⚪"
        if "scam_analysis" in result:
            risk_level = result["scam_analysis"]["risk"]
            risk_emoji = "🟢" if risk_level == "낮음" else "🟡" if risk_level == "중간" else "🔴"
        
        # 가격 변동 이모지
        change_emoji = "➖"
        change_text = "정보 없음"
        if "price_change_24h" in result:
            change = result["price_change_24h"]
            if change != 0:
                change_text = f"{change:.2f}%"
                change_emoji = "🚀" if change > 0 else "📉"
        
        response += f"{i}. <b>{result['name']} ({result['symbol']})</b> {risk_emoji}\n"
        response += f"   가격: <b>${result['price']:.8f}</b> {change_emoji} {change_text}\n"
        response += f"   네트워크: <code>{result['network']}</code>\n"
        
        # 시가총액 정보
        if "market_cap" in result and isinstance(result["market_cap"], (int, float)) and result["market_cap"] > 0:
            market_cap_formatted = f"${result['market_cap']:,.0f}"
            response += f"   시가총액: <b>{market_cap_formatted}</b>\n"
        
        # 유동성 정보
        if "liquidity" in result and isinstance(result["liquidity"], (int, float)) and result["liquidity"] > 0:
            liquidity_formatted = f"${result['liquidity']:,.0f}"
            response += f"   유동성: <b>{liquidity_formatted}</b>\n"
        
        # 스캠 위험도
        if "scam_analysis" in result:
            response += f"   스캠 위험도: <b>{result['scam_analysis']['risk']}</b>\n"
        
        response += "\n"
    
    # 위험도 요약
    if analysis_results["high_risk_count"] > 0:
        response += f"\n⚠️ <b>주의</b>: {analysis_results['high_risk_count']}개의 토큰이 높은 스캠 위험도를 가지고 있습니다."
    
    await loading_message.edit_text(response, parse_mode="HTML")

# 네트워크 선택 키보드 생성 함수
def get_network_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    
    for network_id, network_name in SUPPORTED_NETWORKS.items():
        button = InlineKeyboardButton(text=network_name, callback_data=f"network_{network_id}")
        markup.add(button)
    
    return markup

# 사용자 토큰 스캠 체크 명령 처리
async def scamcheck_user_tokens(message: types.Message):
    try:
        user_id = message.from_user.id
        
        # 로딩 메시지 표시
        loading_message = await message.reply("🔍 <b>토큰의 스캠 여부를 확인 중입니다...</b>", parse_mode="HTML")
        
        # 스캠 체크 실행
        scam_results = await check_user_tokens_scam(user_id)
        
        if not scam_results["success"]:
            await loading_message.edit_text(
                f"❌ <b>오류가 발생했습니다</b>: {scam_results.get('error', '알 수 없는 오류')}",
                parse_mode="HTML"
            )
            return
        
        # 결과가 없는 경우
        if scam_results.get("total_count", 0) == 0:
            await loading_message.edit_text(
                "ℹ️ <b>추적 중인 토큰이 없습니다.</b>\n\n"
                "<code>/dex</code> 명령어로 토큰을 추가한 후 다시 시도하세요.",
                parse_mode="HTML"
            )
            return
        
        # 결과 메시지 생성
        result_text = f"🔍 <b>토큰 스캠 분석 결과</b>\n\n"
        result_text += f"총 <b>{scam_results['total_count']}</b>개 토큰 중 <b>{scam_results['high_risk_count']}</b>개가 높은 위험도를 가지고 있습니다.\n\n"
        
        # 위험도 높은 토큰 먼저 정렬
        sorted_results = sorted(
            scam_results["tokens"],  # "results" 대신 "tokens" 사용
            key=lambda x: (
                0 if x["risk"] == "매우 높음" else
                1 if x["risk"] == "높음" else
                2 if x["risk"] == "중간" else
                3
            )
        )
        
        # 모든 토큰 표시
        for i, result in enumerate(sorted_results, 1):
            risk_emoji = "🔴" if result["risk"] in ["매우 높음", "높음"] else "🟠" if result["risk"] == "중간" else "🟢"
            
            result_text += f"{i}. {risk_emoji} <b>{result['name']} ({result['symbol']})</b>\n"
            result_text += f"   네트워크: {result['network']}\n"
            result_text += f"   주소: <code>{result['token_address']}</code>\n"
            result_text += f"   위험도: <b>{result['risk']}</b> (점수: {result['score']})\n"
            
            if result["indicators"]:
                result_text += f"   위험 지표: {', '.join(result['indicators'][:3])}\n"
            
            # 유동성 정보
            if "liquidity_amount" in result and result["liquidity_amount"] > 0:
                result_text += f"   유동성: ${result['liquidity_amount']:,.2f}\n"
            
            # 홀더 정보
            if "top_holder_percentage" in result and result["top_holder_percentage"] > 0:
                result_text += f"   최대 홀더: {result['top_holder_percentage']:.2f}%\n"
            
            # 생성 일자
            if "days_since_creation" in result and result["days_since_creation"] > 0:
                result_text += f"   생성 일자: {result['days_since_creation']}일 전\n"
            
            result_text += "\n"
        
        # 결과 메시지 전송 (긴 메시지 처리)
        if len(result_text) > 4096:
            # 메시지가 너무 길면 여러 개로 나눠서 전송
            for i in range(0, len(result_text), 4096):
                chunk = result_text[i:i+4096]
                if i == 0:
                    await loading_message.edit_text(chunk, parse_mode="HTML")
                else:
                    await message.reply(chunk, parse_mode="HTML")
        else:
            await loading_message.edit_text(result_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"사용자 토큰 스캠 체크 명령 처리 중 오류: {str(e)}")
        await message.reply(
            f"❌ <b>오류가 발생했습니다</b>: {str(e)}",
            parse_mode="HTML"
        )

if __name__ == '__main__':
    asyncio.run(main())
