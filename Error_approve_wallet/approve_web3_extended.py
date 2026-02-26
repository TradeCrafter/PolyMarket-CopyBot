"""
Script para aprovar allowances com MUITOS endpoints RPC
Inclui opção de usar Infura/Alchemy se você tiver API key

IMPORTANTE: Certifique-se de ter MATIC na carteira (~$0.01 para gas)
"""
import sys
from pathlib import Path
from web3 import Web3
from eth_account import Account


# Polygon USDC
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Polymarket Exchanges
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# ERC20 approve ABI
USDC_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]

# Max uint256 (unlimited allowance)
MAX_UINT256 = 2**256 - 1


def get_rpc_endpoints():
    """Retorna lista extensa de RPCs para tentar"""
    
    # Tentar ler API keys do ambiente
    import os
    infura_key = os.getenv("INFURA_API_KEY", "")
    alchemy_key = os.getenv("ALCHEMY_API_KEY", "")
    
    rpcs = []
    
    # Se tem Infura (geralmente mais confiável)
    if infura_key:
        rpcs.append(f"https://polygon-mainnet.infura.io/v3/{infura_key}")
    
    # Se tem Alchemy (também muito confiável)
    if alchemy_key:
        rpcs.append(f"https://polygon-mainnet.g.alchemy.com/v2/{alchemy_key}")
    
    # Endpoints públicos (muitos para aumentar chance de sucesso)
    rpcs.extend([
        "https://polygon.meowrpc.com",
        "https://rpc.ankr.com/polygon",
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.blockpi.network/v1/rpc/public",
        "https://polygon.drpc.org",
        "https://polygon-rpc.com",
        "https://rpc-mainnet.matic.network",
        "https://polygon.llamarpc.com",
        "https://rpc-mainnet.maticvigil.com",
        "https://poly-rpc.gateway.pokt.network",
        "https://1rpc.io/matic",
        "https://polygon-mainnet.public.blastapi.io",
    ])
    
    return rpcs


def find_config():
    """Encontra config.json"""
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
        if config_path.exists():
            return config_path
    
    search_paths = [
        Path("config.json"),
        Path("../config.json"),
        Path(__file__).parent / "config.json",
        Path(__file__).parent.parent / "config.json",
    ]
    
    for path in search_paths:
        if path.exists():
            return path.resolve()
    
    return None


def load_config(config_path):
    """Carrega config.json"""
    import json
    with open(config_path) as f:
        return json.load(f)


def main():
    print("="*70)
    print("🔐 APROVAÇÃO DIRETA DE ALLOWANCES (Web3.py)")
    print("="*70)
    print()
    
    # Carregar config
    print("📋 Procurando configuração...")
    config_path = find_config()
    
    if not config_path:
        print("❌ config.json não encontrado!")
        return
    
    print(f"✅ Encontrado: {config_path}")
    
    config = load_config(config_path)
    private_key = config.get("private_key", "")
    
    if not private_key:
        print("❌ private_key não configurada no config.json")
        return
    
    # Criar account
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    
    account = Account.from_key(private_key)
    wallet_address = account.address
    
    print(f"✅ Carteira: {wallet_address}")
    print()
    
    # Conectar Web3
    print("🌐 Conectando à Polygon...")
    print("   (Testando múltiplos endpoints...)")
    print()
    
    rpcs = get_rpc_endpoints()
    
    w3 = None
    for i, rpc in enumerate(rpcs, 1):
        try:
            # Mostrar progresso
            rpc_short = rpc.split("//")[1].split("/")[0]
            print(f"   [{i}/{len(rpcs)}] {rpc_short}...", end=" ", flush=True)
            
            test_w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 15}))
            
            # Testar se realmente conectou
            if test_w3.is_connected():
                # Testar chamada real
                test_w3.eth.block_number
                w3 = test_w3
                print("✅")
                break
            else:
                print("❌ (não conectou)")
        except Exception as e:
            print(f"❌ ({str(e)[:30]})")
    
    print()
    
    if not w3:
        print("❌ Não conseguiu conectar em NENHUM RPC da Polygon")
        print()
        print("="*70)
        print("💡 SOLUÇÕES ALTERNATIVAS")
        print("="*70)
        print()
        print("OPÇÃO 1: Usar Infura ou Alchemy (mais confiável)")
        print()
        print("  1. Crie conta grátis em:")
        print("     - Infura: https://infura.io")
        print("     - Alchemy: https://alchemy.com")
        print()
        print("  2. Pegue sua API key")
        print()
        print("  3. Configure variável de ambiente:")
        print("     Windows: set INFURA_API_KEY=sua_key_aqui")
        print("     Linux/Mac: export INFURA_API_KEY=sua_key_aqui")
        print()
        print("  4. Rode este script novamente")
        print()
        print("OPÇÃO 2: Aprovar manualmente via MetaMask")
        print()
        print("  Execute: python approve_manual_instructions.py")
        print()
        print("OPÇÃO 3: Usar uma VPN")
        print()
        print("  Seu ISP pode estar bloqueando os endpoints RPC")
        print("  Tente conectar uma VPN e rode novamente")
        print()
        return
    
    print(f"✅ Conectado à Polygon")
    print()
    
    # Contrato USDC
    usdc_contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_ADDRESS),
        abi=USDC_ABI
    )
    
    # Verificar allowances atuais
    print("🔍 Verificando allowances atuais...")
    
    try:
        allowance_exchange = usdc_contract.functions.allowance(
            wallet_address,
            Web3.to_checksum_address(EXCHANGE_ADDRESS)
        ).call()
        
        allowance_negrisk = usdc_contract.functions.allowance(
            wallet_address,
            Web3.to_checksum_address(NEG_RISK_EXCHANGE)
        ).call()
        
        print(f"   Exchange normal: {allowance_exchange}")
        print(f"   NegRisk Exchange: {allowance_negrisk}")
        print()
        
        if allowance_exchange >= MAX_UINT256 / 2 and allowance_negrisk >= MAX_UINT256 / 2:
            print("✅ Allowances já estão configurados!")
            print("   Não é necessário aprovar novamente")
            print()
            print("❗ Se o bot ainda falha, o problema NÃO é allowance.")
            print("   Verifique se esta carteira tem USDC suficiente")
            return
    except Exception as e:
        print(f"   ⚠️  Erro ao verificar: {e}")
        print("   Continuando mesmo assim...")
    
    print()
    print("="*70)
    print("⚠️  ATENÇÃO")
    print("="*70)
    print("Isso vai fazer 2 transações on-chain para aprovar:")
    print(f"  1. Exchange normal: {EXCHANGE_ADDRESS}")
    print(f"  2. NegRisk Exchange: {NEG_RISK_EXCHANGE}")
    print()
    print("Custo estimado: ~$0.01-0.02 de MATIC (gas)")
    print()
    
    response = input("❓ Deseja continuar? (y/n): ").strip().lower()
    
    if response != 'y':
        print("❌ Cancelado")
        return
    
    print()
    print("🔓 Aprovando allowances...")
    print()
    
    # Aprovar Exchange normal
    print("1️⃣  Aprovando Exchange normal...")
    try:
        nonce = w3.eth.get_transaction_count(wallet_address)
        
        tx = usdc_contract.functions.approve(
            Web3.to_checksum_address(EXCHANGE_ADDRESS),
            MAX_UINT256
        ).build_transaction({
            'from': wallet_address,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 137,
        })
        
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"   TX enviada: {tx_hash.hex()}")
        print("   Aguardando confirmação...", end=" ", flush=True)
        
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        
        if receipt['status'] == 1:
            print("✅ Confirmada!")
        else:
            print("❌ Falhou!")
            print(f"   Receipt: {receipt}")
            return
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        return
    
    print()
    
    # Aprovar NegRisk Exchange
    print("2️⃣  Aprovando NegRisk Exchange...")
    try:
        nonce = w3.eth.get_transaction_count(wallet_address)
        
        tx = usdc_contract.functions.approve(
            Web3.to_checksum_address(NEG_RISK_EXCHANGE),
            MAX_UINT256
        ).build_transaction({
            'from': wallet_address,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 137,
        })
        
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"   TX enviada: {tx_hash.hex()}")
        print("   Aguardando confirmação...", end=" ", flush=True)
        
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        
        if receipt['status'] == 1:
            print("✅ Confirmada!")
        else:
            print("❌ Falhou!")
            print(f"   Receipt: {receipt}")
            return
        
    except Exception as e:
        print(f"\n❌ Erro: {e}")
        return
    
    print()
    print("="*70)
    print("🎉 SUCESSO!")
    print("="*70)
    print()
    print("✅ Allowances aprovados com sucesso!")
    print(f"✅ Exchange normal: APROVADO")
    print(f"✅ NegRisk Exchange: APROVADO")
    print()
    print("🚀 Agora você pode executar o bot normalmente:")
    print("   python main.py")
    print()


if __name__ == "__main__":
    main()
