"""
Telegram Notifier - sends real-time alerts to Telegram chat/group
Supports bot commands, inline keyboards, menu button, and callback queries.
"""
import logging
import asyncio
import html
import json
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .bot import CopyTradingBot

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramNotifier:
    """Sends trade notifications and accepts commands via Telegram bot"""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        notify_trades: bool = True,
        notify_errors: bool = True,
        notify_risk_events: bool = True,
        notify_summary: bool = True,
        summary_interval_hours: int = 6,
        commands_enabled: bool = True,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.notify_trades = notify_trades
        self.notify_errors = notify_errors
        self.notify_risk_events = notify_risk_events
        self.notify_summary = notify_summary
        self.summary_interval_hours = summary_interval_hours
        self.commands_enabled = commands_enabled

        self._http = httpx.AsyncClient(timeout=15)
        self._base_url = TELEGRAM_API.format(token=bot_token)
        self._last_update_id = 0
        self._bot_ref: Optional["CopyTradingBot"] = None
        self._polling = False
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._sender_task: Optional[asyncio.Task] = None
        self._rate_limited_until: float = 0  # timestamp when rate limit expires

    def set_bot_ref(self, bot: "CopyTradingBot"):
        """Set reference to main bot for command handling"""
        self._bot_ref = bot

    # ─── SETUP ────────────────────────────────────────────────────────

    async def setup_bot_commands(self):
        """Register bot commands with Telegram (creates the menu button)"""
        if not self.enabled:
            return

        commands = [
            {"command": "status", "description": "📊 Status geral do bot"},
            {"command": "dashboard", "description": "🖥 Painel interativo"},
            {"command": "positions", "description": "📦 Posições reais na Polymarket"},
            {"command": "risk", "description": "🛡 Exposição e limites de risco"},
            {"command": "wallets", "description": "👁 Carteiras monitoradas"},
            {"command": "trades", "description": "📜 Últimos trades"},
            {"command": "pnl", "description": "💰 Lucro e prejuízo"},
            {"command": "summary", "description": "📋 Resumo completo"},
            {"command": "halt", "description": "🛑 Parar trading"},
            {"command": "resume", "description": "✅ Retomar trading"},
            {"command": "settings", "description": "⚙️ Configurações atuais"},
            {"command": "help", "description": "❓ Lista de comandos"},
        ]

        try:
            resp = await self._http.post(
                f"{self._base_url}/setMyCommands",
                json={"commands": commands},
            )
            if resp.status_code == 200:
                logger.info("Telegram bot commands registered (menu button enabled)")
            else:
                logger.warning(f"Failed to set bot commands: {resp.text[:200]}")

            # Set the menu button to show commands
            await self._http.post(
                f"{self._base_url}/setChatMenuButton",
                json={
                    "chat_id": self.chat_id,
                    "menu_button": {"type": "commands"},
                },
            )
        except Exception as e:
            logger.error(f"Error setting up bot commands: {e}")

    # ─── MESSAGE SENDING ─────────────────────────────────────────────

    async def _send_raw(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_preview: bool = True,
        reply_markup: dict = None,
    ) -> Optional[dict]:
        """Send a message to the configured chat"""
        if not self.enabled or not self.bot_token or not self.chat_id:
            return None

        # Respect rate limit
        import time
        now = time.time()
        if now < self._rate_limited_until:
            wait = self._rate_limited_until - now
            logger.debug(f"Rate limited, skipping message ({wait:.0f}s remaining)")
            return None

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text[:4096],
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            resp = await self._http.post(
                f"{self._base_url}/sendMessage",
                json=payload,
            )

            if resp.status_code == 429:
                data = resp.json()
                retry_after = data.get("parameters", {}).get("retry_after", 30)
                self._rate_limited_until = time.time() + retry_after
                logger.warning(f"Telegram rate limited, pausing for {retry_after}s")
                return None

            if resp.status_code != 200:
                logger.warning(f"Telegram send failed ({resp.status_code}): {resp.text[:200]}")
                return None
            return resp.json().get("result")
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return None

    async def _edit_message(self, message_id: int, text: str, reply_markup: dict = None):
        """Edit an existing message"""
        import time
        if time.time() < self._rate_limited_until:
            return

        try:
            payload = {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            resp = await self._http.post(
                f"{self._base_url}/editMessageText",
                json=payload,
            )

            if resp.status_code == 429:
                data = resp.json()
                retry_after = data.get("parameters", {}).get("retry_after", 30)
                self._rate_limited_until = time.time() + retry_after
                logger.warning(f"Telegram rate limited on edit, pausing for {retry_after}s")
        except Exception as e:
            logger.error(f"Telegram edit error: {e}")

    async def _answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False):
        """Acknowledge a callback query"""
        try:
            await self._http.post(
                f"{self._base_url}/answerCallbackQuery",
                json={
                    "callback_query_id": callback_id,
                    "text": text,
                    "show_alert": show_alert,
                },
            )
        except Exception:
            pass

    async def send(self, text: str, parse_mode: str = "HTML", reply_markup: dict = None):
        """Queue a message for sending (rate-limit safe)"""
        await self._message_queue.put((text, parse_mode, reply_markup))

    async def _message_sender_loop(self):
        """Process message queue with rate limiting"""
        while True:
            try:
                item = await self._message_queue.get()
                text, parse_mode, reply_markup = (
                    item if len(item) == 3 else (*item, None)
                )

                # If rate limited, drop queued messages to avoid pileup
                import time
                if time.time() < self._rate_limited_until:
                    wait = self._rate_limited_until - time.time()
                    logger.debug(f"Rate limited, dropping queued message ({wait:.0f}s left)")
                    # Drain remaining queue too
                    while not self._message_queue.empty():
                        try:
                            self._message_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    await asyncio.sleep(min(wait, 10))
                    continue

                await self._send_raw(text, parse_mode, reply_markup=reply_markup)
                await asyncio.sleep(1.5)  # Telegram allows ~20 msg/min to same chat
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Message sender error: {e}")
                await asyncio.sleep(2)

    # ─── INLINE KEYBOARDS ────────────────────────────────────────────

    @staticmethod
    def _inline_kb(buttons: list[list[tuple[str, str]]]) -> dict:
        """
        Build an InlineKeyboardMarkup.
        buttons = [[("Label", "callback_data"), ...], ...]
        """
        return {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row]
                for row in buttons
            ]
        }

    def _dashboard_kb(self) -> dict:
        """Main dashboard keyboard"""
        halted = self._bot_ref and self._bot_ref.risk._halted if self._bot_ref else False
        toggle_btn = ("▶️ Retomar", "cb_resume") if halted else ("⏸ Pausar", "cb_halt")

        return self._inline_kb([
            [("📊 Status", "cb_status"), ("🛡 Risco", "cb_risk")],
            [("📦 Posições", "cb_positions"), ("📜 Trades", "cb_trades")],
            [("💰 P&L", "cb_pnl"), ("📋 Resumo", "cb_summary")],
            [toggle_btn, ("⚙️ Config", "cb_settings")],
            [("🔄 Atualizar", "cb_refresh")],
        ])

    def _back_kb(self) -> dict:
        """Back to dashboard button"""
        return self._inline_kb([
            [("◀️ Voltar", "cb_dashboard")],
        ])

    def _trades_nav_kb(self, offset: int = 0, page_size: int = 5) -> dict:
        """Navigation for trade history"""
        buttons = []
        nav_row = []
        if offset > 0:
            nav_row.append(("⬅️ Anterior", f"cb_trades_page:{offset - page_size}"))
        nav_row.append(("➡️ Próximo", f"cb_trades_page:{offset + page_size}"))
        buttons.append(nav_row)
        buttons.append([("◀️ Voltar", "cb_dashboard")])
        return self._inline_kb(buttons)

    # ─── TRADE NOTIFICATIONS ─────────────────────────────────────────

    async def notify_new_trade_detected(
        self,
        source_label: str,
        side: str,
        size: float,
        price: float,
        usdc_amount: float,
        market_title: str,
        outcome: str,
    ):
        """
        Trade detected from source wallet.
        Silenced to avoid rate limits — only execution results are sent.
        """
        return

    async def notify_trade_executed(
        self,
        side: str,
        usdc_amount: float,
        market_title: str,
        outcome: str,
        order_id: str = "",
        status: str = "",
        is_dry_run: bool = False,
        source_label: str = "",
        copy_ratio: float = 1.0,
        source_usdc: float = 0,
        source_price: float = 0,
        source_size: float = 0,
    ):
        """Notify when a copy trade is executed — single message per trade"""
        if not self.notify_trades:
            return

        emoji = "🟢" if side == "BUY" else "🔴"
        mode = "🔵 DRY RUN" if is_dry_run else "✅ EXECUTADO"

        msg = (
            f"{emoji} {mode}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {esc(market_title)}\n"
            f"🎯 {side} <b>{esc(outcome)}</b> — <b>${usdc_amount:,.2f}</b>\n"
        )

        if source_usdc > 0:
            msg += f"📡 {esc(source_label)}: ${source_usdc:,.2f} → ${usdc_amount:,.2f} ({copy_ratio}x)\n"

        if source_price > 0:
            msg += f"💰 {source_size:,.0f} tokens @ ${source_price:.4f}\n"

        if order_id:
            msg += f"🔖 <code>{order_id[:16]}</code>\n"

        await self.send(msg)

    async def notify_trade_rejected(
        self,
        side: str,
        usdc_amount: float,
        market_title: str,
        reason: str,
        source_label: str = "",
    ):
        """Silenced — rejections only logged to console to avoid rate limits"""
        return

    async def notify_trade_failed(
        self,
        side: str,
        usdc_amount: float,
        market_title: str,
        error: str,
    ):
        """Silenced — failures only logged to console to avoid rate limits"""
        return

    # ─── SYSTEM NOTIFICATIONS ────────────────────────────────────────

    async def notify_startup(
        self,
        mode: str,
        num_wallets: int,
        max_exposure: float,
        sizing_strategy: str = "",
    ):
        """Send startup notification with dashboard"""
        mode_emoji = "🟡" if mode == "DRY_RUN" else "🔴"
        msg = (
            f"🚀 <b>Bot Iniciado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{mode_emoji} Modo: <b>{mode}</b>\n"
            f"👁 Carteiras: {num_wallets}\n"
            f"💼 Exposição max: ${max_exposure:,.0f}\n"
        )
        if sizing_strategy:
            msg += f"📐 Sizing: {sizing_strategy}\n"
        msg += f"\n⏰ {_now()}"
        await self.send(msg, reply_markup=self._dashboard_kb())

    async def notify_shutdown(self, reason: str = "Manual"):
        """Send shutdown notification"""
        msg = (
            f"🛑 <b>Bot Parado</b>\n"
            f"Motivo: {esc(reason)}\n"
            f"⏰ {_now()}"
        )
        await self._send_raw(msg)

    async def notify_halt(self, reason: str):
        """Notify trading halt"""
        if not self.notify_risk_events:
            return

        msg = (
            f"🛑 <b>TRADING HALTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ {esc(reason)}\n"
        )
        kb = self._inline_kb([
            [("▶️ Retomar Trading", "cb_resume")],
            [("🛡 Ver Risco", "cb_risk")],
        ])
        await self.send(msg, reply_markup=kb)

    async def notify_error(self, context: str, error: str):
        """Notify a system error"""
        if not self.notify_errors:
            return

        msg = (
            f"⚠️ <b>Erro</b>\n"
            f"Contexto: {esc(context)}\n"
            f"<code>{esc(error[:300])}</code>"
        )
        await self.send(msg)

    async def notify_slippage_warning(
        self,
        market_title: str,
        target_price: float,
        current_price: float,
        slippage_pct: float,
    ):
        """Silenced — slippage only logged to console"""
        return

    # ─── SUMMARY ─────────────────────────────────────────────────────

    async def send_summary(self):
        """Send periodic summary"""
        if not self._bot_ref or not self.notify_summary:
            return

        msg = self._build_summary_text()
        await self.send(msg, reply_markup=self._dashboard_kb())

    async def _summary_loop(self):
        """Periodic summary sender"""
        while self._polling:
            await asyncio.sleep(self.summary_interval_hours * 3600)
            if self._polling:
                await self.send_summary()

    # ─── COMMAND HANDLING ─────────────────────────────────────────────

    async def start_polling(self):
        """Start polling for Telegram commands + message sender"""
        if not self.enabled or not self.bot_token:
            return

        self._polling = True

        # Register commands (creates menu button)
        await self.setup_bot_commands()

        self._sender_task = asyncio.create_task(self._message_sender_loop())

        if self.commands_enabled:
            asyncio.create_task(self._poll_updates())

        if self.notify_summary and self.summary_interval_hours > 0:
            asyncio.create_task(self._summary_loop())

    async def stop_polling(self):
        """Stop polling"""
        self._polling = False
        if self._sender_task:
            self._sender_task.cancel()

    async def _poll_updates(self):
        """Poll Telegram for command updates and callback queries"""
        logger.info("Telegram command listener started")

        while self._polling:
            try:
                resp = await self._http.get(
                    f"{self._base_url}/getUpdates",
                    params={
                        "offset": self._last_update_id + 1,
                        "timeout": 30,
                        "allowed_updates": json.dumps(["message", "callback_query"]),
                    },
                    timeout=35,
                )

                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                data = resp.json()
                results = data.get("result", [])

                for update in results:
                    self._last_update_id = update["update_id"]

                    # Handle callback queries (button presses)
                    callback = update.get("callback_query")
                    if callback:
                        cb_chat_id = str(
                            callback.get("message", {}).get("chat", {}).get("id", "")
                        )
                        if cb_chat_id == str(self.chat_id):
                            await self._handle_callback(callback)
                        continue

                    # Handle text commands
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if chat_id != str(self.chat_id):
                        continue

                    if text.startswith("/"):
                        await self._handle_command(text.strip())

            except asyncio.CancelledError:
                break
            except httpx.ReadTimeout:
                continue
            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

    # ─── CALLBACK QUERY HANDLING ─────────────────────────────────────

    async def _handle_callback(self, callback: dict):
        """Handle inline keyboard button presses"""
        cb_id = callback.get("id", "")
        data = callback.get("data", "")
        message_id = callback.get("message", {}).get("message_id")

        if not data or not message_id:
            await self._answer_callback(cb_id)
            return

        await self._answer_callback(cb_id)

        if data == "cb_dashboard":
            await self._cb_dashboard(message_id)
        elif data == "cb_status":
            await self._cb_status(message_id)
        elif data == "cb_risk":
            await self._cb_risk(message_id)
        elif data == "cb_positions":
            await self._cb_positions(message_id)
        elif data == "cb_wallets":
            await self._cb_wallets(message_id)
        elif data == "cb_trades":
            await self._cb_trades(message_id, 0)
        elif data.startswith("cb_trades_page:"):
            offset = int(data.split(":")[1])
            await self._cb_trades(message_id, max(0, offset))
        elif data == "cb_pnl":
            await self._cb_pnl(message_id)
        elif data == "cb_summary":
            await self._cb_summary(message_id)
        elif data == "cb_settings":
            await self._cb_settings(message_id)
        elif data == "cb_halt":
            await self._cb_halt(message_id)
        elif data == "cb_resume":
            await self._cb_resume(message_id)
        elif data == "cb_refresh":
            await self._cb_dashboard(message_id)

    async def _cb_dashboard(self, msg_id: int):
        if not self._bot_ref:
            return
        status = self._bot_ref.get_status()
        risk = status.get("risk", {})
        daily = risk.get("daily_stats", {})

        halted = risk.get("halted", False)
        state = "🛑 PARADO" if halted else "✅ ATIVO"
        mode = status.get("mode", "?")

        msg = (
            f"🖥 <b>Painel — Copy Trading Bot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Estado: {state}  •  Modo: <b>{mode}</b>\n\n"
            f"💼 Exposição: <b>${risk.get('total_exposure', 0):,.2f}</b>"
            f" / ${risk.get('max_total_exposure', 0):,.0f}\n"
            f"📈 Posições: {risk.get('positions_count', 0)}"
            f"  •  Mercados: {risk.get('markets_count', 0)}\n\n"
            f"<b>Hoje</b>: {daily.get('trades', 0)} trades"
            f"  •  C: ${daily.get('bought_usdc', 0):,.0f}"
            f"  •  V: ${daily.get('sold_usdc', 0):,.0f}\n\n"
            f"⏰ {_now()}"
        )
        await self._edit_message(msg_id, msg, reply_markup=self._dashboard_kb())

    async def _cb_status(self, msg_id: int):
        if not self._bot_ref:
            return
        await self._edit_message(msg_id, self._build_status_text(), reply_markup=self._back_kb())

    async def _cb_risk(self, msg_id: int):
        if not self._bot_ref:
            return
        await self._edit_message(msg_id, self._build_risk_text(), reply_markup=self._back_kb())

    async def _cb_wallets(self, msg_id: int):
        if not self._bot_ref:
            return
        await self._edit_message(msg_id, self._build_wallets_text(), reply_markup=self._back_kb())

    async def _cb_trades(self, msg_id: int, offset: int = 0):
        if not self._bot_ref:
            return
        page_size = 5
        await self._edit_message(
            msg_id,
            self._build_trades_text(offset, page_size),
            reply_markup=self._trades_nav_kb(offset, page_size),
        )

    async def _cb_positions(self, msg_id: int):
        """Show real Polymarket positions fetched from API"""
        if not self._bot_ref:
            return
        kb = self._inline_kb([
            [("🔄 Atualizar", "cb_positions")],
            [("◀️ Voltar", "cb_dashboard")],
        ])
        msg = await self._build_positions_text()
        await self._edit_message(msg_id, msg, reply_markup=kb)

    async def _build_positions_text(self) -> str:
        """Build positions display from Polymarket API"""
        if not self._bot_ref:
            return "Bot não inicializado"

        try:
            positions = await self._bot_ref.get_my_positions()
        except Exception as e:
            return f"Erro ao buscar posições: {e}"

        if not positions:
            return (
                "📦 POSIÇÕES NA POLYMARKET\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Nenhuma posição aberta encontrada.\n\n"
                "Verifique se o funder_address está\n"
                "configurado corretamente."
            )

        total_invested = sum(p["initial_value"] for p in positions)
        total_current = sum(p["current_value"] for p in positions)
        total_pnl = total_current - total_invested
        redeemable = [p for p in positions if p.get("redeemable")]

        lines = [
            "📦 POSIÇÕES NA POLYMARKET",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  Posições: {len(positions)}",
            f"  Investido: ${total_invested:,.2f}",
            f"  Valor atual: ${total_current:,.2f}",
            f"  P&L: ${total_pnl:+,.2f} ({total_pnl / max(total_invested, 0.01) * 100:+.1f}%)",
        ]

        if redeemable:
            redeem_val = sum(p["current_value"] for p in redeemable)
            lines.append(f"  Resgatáveis: {len(redeemable)} (${redeem_val:,.2f})")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Show positions (max 20)
        for i, p in enumerate(positions[:20], 1):
            title = p["title"][:35]
            pnl = p["current_value"] - p["initial_value"]
            pnl_icon = "🟢" if pnl >= 0 else "🔴"
            redeem_tag = " 💎" if p.get("redeemable") else ""

            lines.append(f"\n{pnl_icon} {title}{redeem_tag}")
            lines.append(f"  {p['outcome']} | {p['size']:.1f} sh @ ${p['avg_price']:.4f}")
            lines.append(f"  Custo: ${p['initial_value']:.2f} → ${p['current_value']:.2f} ({pnl:+.2f})")
            if p["cur_price"] > 0:
                lines.append(f"  Preço atual: ${p['cur_price']:.4f}")

        if len(positions) > 20:
            lines.append(f"\n... +{len(positions) - 20} posições")

        return "\n".join(lines)

    async def _cb_pnl(self, msg_id: int):
        if not self._bot_ref:
            return
        kb = self._inline_kb([
            [("🔄 Atualizar", "cb_pnl"), ("📜 Trades", "cb_trades")],
            [("◀️ Voltar", "cb_dashboard")],
        ])
        await self._edit_message(msg_id, self._build_pnl_text(), reply_markup=kb)

    async def _cb_summary(self, msg_id: int):
        if not self._bot_ref:
            return
        await self._edit_message(msg_id, self._build_summary_text(), reply_markup=self._back_kb())

    async def _cb_settings(self, msg_id: int):
        if not self._bot_ref:
            return
        await self._edit_message(msg_id, self._build_settings_text(), reply_markup=self._back_kb())

    async def _cb_halt(self, msg_id: int):
        if not self._bot_ref:
            return
        self._bot_ref.risk.halt("Parado via Telegram")
        await self._cb_dashboard(msg_id)

    async def _cb_resume(self, msg_id: int):
        if not self._bot_ref:
            return
        self._bot_ref.risk.resume()
        await self._cb_dashboard(msg_id)

    # ─── TEXT BUILDERS ────────────────────────────────────────────────

    def _build_status_text(self) -> str:
        status = self._bot_ref.get_status()
        risk = status.get("risk", {})
        daily = risk.get("daily_stats", {})
        uptime = status.get("uptime", "?")

        halted = risk.get("halted", False)
        halt_line = f"🛑 HALTED: {risk.get('halt_reason', '')}" if halted else "✅ Ativo"

        exp_pct = risk.get("exposure_pct", 0)
        bar = _progress_bar(exp_pct, 20)

        return (
            f"📊 <b>Status do Bot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Modo: <b>{status.get('mode')}</b>\n"
            f"{halt_line}\n"
            f"⏱ Uptime: {uptime}\n\n"
            f"<b>Exposição</b>\n"
            f"<code>{bar}</code> {exp_pct:.1f}%\n"
            f"${risk.get('total_exposure', 0):,.2f} / ${risk.get('max_total_exposure', 0):,.0f}\n\n"
            f"<b>Hoje ({daily.get('date', '?')})</b>\n"
            f"  📈 Trades: {daily.get('trades', 0)}\n"
            f"  🟢 Compras: ${daily.get('bought_usdc', 0):,.2f}\n"
            f"  🔴 Vendas: ${daily.get('sold_usdc', 0):,.2f}\n\n"
            f"⏰ {_now()}"
        )

    def _build_risk_text(self) -> str:
        risk = self._bot_ref.risk.get_status()
        daily = risk.get("daily_stats", {})

        exp_bar = _progress_bar(risk.get("exposure_pct", 0), 20)
        loss_used = daily.get("bought_usdc", 0) - daily.get("sold_usdc", 0)
        max_loss = risk.get("max_daily_loss", 200)
        loss_pct = (loss_used / max_loss * 100) if max_loss > 0 else 0
        loss_bar = _progress_bar(min(max(loss_pct, 0), 100), 20)

        return (
            f"🛡 <b>Gestão de Risco</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Exposição</b>\n"
            f"<code>{exp_bar}</code> {risk.get('exposure_pct', 0):.1f}%\n"
            f"${risk.get('total_exposure', 0):,.2f} / ${risk.get('max_total_exposure', 0):,.0f}\n\n"
            f"<b>Limites</b>\n"
            f"  Max por trade: ${risk.get('max_trade_usdc', 0):,.0f}\n"
            f"  Max por mercado: ${risk.get('max_per_market', 0):,.0f}\n"
            f"  Posições abertas: {risk.get('positions_count', 0)}\n"
            f"  Mercados ativos: {risk.get('markets_count', 0)}\n\n"
            f"<b>Loss Diário</b>\n"
            f"<code>{loss_bar}</code> {loss_pct:.1f}%\n"
            f"${loss_used:,.2f} / ${max_loss:,.0f}\n\n"
            f"Halted: {'⛔ ' + risk.get('halt_reason', '') if risk.get('halted') else '✅ Não'}\n\n"
            f"⏰ {_now()}"
        )

    def _build_wallets_text(self) -> str:
        wallets = self._bot_ref.watcher.get_status()
        msg = (
            f"👁 <b>Carteiras Monitoradas ({len(wallets)})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )
        for w in wallets:
            label = w["label"]
            addr = w.get("resolved", w["address"])
            short = addr[:8] + "…" + addr[-4:] if len(addr) > 16 else addr
            errs = w.get("errors", 0)
            err_str = f"  ⚠️ {errs} erros" if errs > 0 else ""

            msg += (
                f"\n<b>{esc(label)}</b>\n"
                f"  📍 <code>{short}</code>\n"
                f"  📊 Ratio: {w['copy_ratio']}x\n"
                f"  🔔 Trades: {w['trades_detected']}{err_str}\n"
            )
        msg += f"\n⏰ {_now()}"
        return msg

    def _build_trades_text(self, offset: int = 0, page_size: int = 5) -> str:
        trades = self._bot_ref._trade_log
        total = len(trades)

        if total == 0:
            return "📜 <b>Trades</b>\n━━━━━━━━━━━━━━━━━━━━\nNenhum trade registrado."

        page = list(reversed(trades))[offset : offset + page_size]
        page_num = offset // page_size + 1
        total_pages = (total + page_size - 1) // page_size

        msg = (
            f"📜 <b>Trades</b> (pág {page_num}/{total_pages})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
        )

        for t in page:
            ts = t.get("timestamp", "?")[11:16]
            side = t.get("side", "?")
            emoji = "🟢" if side == "BUY" else "🔴"
            market = t.get("market", "?")[:30]
            usdc = t.get("execution_usdc", t.get("source_usdc", 0))

            if t.get("rejected"):
                status = "🚫"
            elif t.get("dry_run"):
                status = "🔵"
            elif t.get("executed"):
                status = "✅"
            else:
                status = "❌"

            msg += f"\n{emoji} {ts} {status} {side} <b>${usdc:,.2f}</b>\n    {esc(market)}\n"

        msg += f"\nTotal: {total} trades"
        return msg

    def _build_pnl_text(self) -> str:
        trades = self._bot_ref._trade_log
        risk = self._bot_ref.risk.get_status()

        if not trades:
            return "💰 <b>P&L</b>\n━━━━━━━━━━━━━━━━━━━━\nNenhum trade registrado."

        # Filter only executed/dry_run trades
        active = [t for t in trades if t.get("executed") or t.get("dry_run")]
        rejected = [t for t in trades if t.get("rejected")]

        if not active:
            return (
                f"💰 <b>P&L</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"Nenhum trade executado.\n"
                f"🚫 {len(rejected)} rejeitados"
            )

        # ── Totals ──
        buys = [t for t in active if t.get("side") == "BUY"]
        sells = [t for t in active if t.get("side") == "SELL"]

        total_bought = sum(t.get("execution_usdc", t.get("source_usdc", 0)) for t in buys)
        total_sold = sum(t.get("execution_usdc", t.get("source_usdc", 0)) for t in sells)
        net_flow = total_sold - total_bought

        # ── Per-market breakdown ──
        markets: dict[str, dict] = {}
        for t in active:
            key = t.get("condition_id") or t.get("market", "?")
            if key not in markets:
                markets[key] = {
                    "title": t.get("market", "?")[:25],
                    "outcome": t.get("outcome", "?"),
                    "bought": 0.0,
                    "sold": 0.0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "tokens_bought": 0.0,
                    "tokens_sold": 0.0,
                    "avg_buy_price": 0.0,
                    "avg_sell_price": 0.0,
                    "last_price": 0.0,
                }
            m = markets[key]
            usdc = t.get("execution_usdc", t.get("source_usdc", 0))
            tokens = t.get("source_size", 0)
            price = t.get("source_price", 0)

            if t.get("side") == "BUY":
                m["bought"] += usdc
                m["buy_count"] += 1
                m["tokens_bought"] += tokens
            else:
                m["sold"] += usdc
                m["sell_count"] += 1
                m["tokens_sold"] += tokens
            m["last_price"] = price

        # ── Round-trip P&L (markets with both buys and sells) ──
        realized_pnl = 0.0
        round_trips = 0
        wins = 0
        losses = 0

        for m in markets.values():
            if m["buy_count"] > 0 and m["sell_count"] > 0:
                pnl = m["sold"] - m["bought"]
                realized_pnl += pnl
                round_trips += 1
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1

        # ── Open positions (markets with buys only) ──
        open_positions = sum(1 for m in markets.values() if m["buy_count"] > 0 and m["sell_count"] == 0)
        open_exposure = sum(m["bought"] for m in markets.values() if m["buy_count"] > 0 and m["sell_count"] == 0)

        # ── Stats ──
        win_rate = (wins / round_trips * 100) if round_trips > 0 else 0
        avg_trade = total_bought / len(buys) if buys else 0
        biggest = max((t.get("execution_usdc", t.get("source_usdc", 0)) for t in active), default=0)

        # ── Margin from risk manager ──
        total_margin = risk.get("max_total_exposure", 0)
        used_margin = risk.get("total_exposure", 0)
        free_margin = total_margin - used_margin
        margin_pct = risk.get("exposure_pct", 0)
        margin_bar = _progress_bar(margin_pct, 16)

        # ── Format ──
        net_emoji = "📈" if net_flow >= 0 else "📉"
        pnl_emoji = "🟢" if realized_pnl >= 0 else "🔴"
        wr_emoji = "🏆" if win_rate >= 50 else "📊"

        msg = (
            f"💰 <b>P&L Dashboard</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        # Margin section
        msg += (
            f"<b>💼 Margem</b>\n"
            f"  Total: <b>${total_margin:,.0f}</b>\n"
            f"  Usada: ${used_margin:,.2f}\n"
            f"  Livre: ${free_margin:,.2f}\n"
            f"  <code>{margin_bar}</code> {margin_pct:.1f}%\n\n"
        )

        # Flow section
        msg += (
            f"<b>💸 Fluxo</b>\n"
            f"  🟢 Compras: <b>${total_bought:,.2f}</b> ({len(buys)}x)\n"
            f"  🔴 Vendas: <b>${total_sold:,.2f}</b> ({len(sells)}x)\n"
            f"  {net_emoji} Net: <b>${net_flow:+,.2f}</b>\n\n"
        )

        # P&L section
        if round_trips > 0:
            msg += (
                f"<b>{pnl_emoji} P&L Realizado</b>\n"
                f"  Resultado: <b>${realized_pnl:+,.2f}</b>\n"
                f"  {wr_emoji} Win rate: <b>{win_rate:.0f}%</b>"
                f" ({wins}W / {losses}L)\n"
                f"  Round-trips: {round_trips}\n\n"
            )
        else:
            msg += (
                f"<b>📊 P&L Realizado</b>\n"
                f"  Sem round-trips ainda\n"
                f"  (precisa de BUY + SELL no mesmo mercado)\n\n"
            )

        # Open positions
        msg += (
            f"<b>📂 Posições Abertas</b>\n"
            f"  Mercados: {open_positions}\n"
            f"  Exposição: ${open_exposure:,.2f}\n\n"
        )

        # Stats section
        msg += (
            f"<b>📊 Estatísticas</b>\n"
            f"  Trades: {len(active)} exec"
        )
        if rejected:
            msg += f" / {len(rejected)} rej"
        msg += (
            f"\n  Mercados: {len(markets)}\n"
            f"  Avg trade: ${avg_trade:,.2f}\n"
            f"  Maior trade: ${biggest:,.2f}\n\n"
        )

        # Top 3 markets by volume
        top = sorted(markets.values(), key=lambda m: m["bought"] + m["sold"], reverse=True)[:3]
        if top:
            msg += "<b>🔝 Top Mercados</b>\n"
            for i, m in enumerate(top, 1):
                vol = m["bought"] + m["sold"]
                pnl = m["sold"] - m["bought"]
                pnl_str = f"${pnl:+,.0f}" if m["sell_count"] > 0 else "aberto"
                msg += f"  {i}. {esc(m['title'])} — ${vol:,.0f} ({pnl_str})\n"
            msg += "\n"

        msg += f"⏰ {_now()}"
        return msg

    def _build_summary_text(self) -> str:
        status = self._bot_ref.get_status()
        risk = status.get("risk", {})
        daily = risk.get("daily_stats", {})
        wallets = status.get("wallets", [])

        msg = (
            f"📋 <b>Resumo Completo</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Modo: <b>{status.get('mode', '?')}</b>\n"
            f"{'🛑 HALTED' if risk.get('halted') else '✅ Ativo'}\n\n"
            f"<b>Exposição</b>\n"
            f"  ${risk.get('total_exposure', 0):,.2f} / ${risk.get('max_total_exposure', 0):,.0f} "
            f"({risk.get('exposure_pct', 0):.1f}%)\n"
            f"  Posições: {risk.get('positions_count', 0)}\n"
            f"  Mercados: {risk.get('markets_count', 0)}\n\n"
            f"<b>Hoje ({daily.get('date', '?')})</b>\n"
            f"  Trades: {daily.get('trades', 0)}\n"
            f"  Compras: ${daily.get('bought_usdc', 0):,.2f}\n"
            f"  Vendas: ${daily.get('sold_usdc', 0):,.2f}\n\n"
            f"<b>Carteiras ({len(wallets)})</b>\n"
        )
        for w in wallets:
            label = w.get("label", w.get("address", "?")[:10])
            msg += f"  • {label}: {w.get('trades_detected', 0)} trades\n"

        msg += f"\n⏰ {_now()}"
        return msg

    def _build_settings_text(self) -> str:
        if not self._bot_ref:
            return "⚙️ Bot ref not set"

        cfg = self._bot_ref.config

        return (
            f"⚙️ <b>Configurações</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Geral</b>\n"
            f"  Modo: {'DRY RUN' if cfg.dry_run else 'LIVE'}\n"
            f"  Poll interval: {cfg.poll_interval_seconds}s\n"
            f"  Skip sports: {cfg.skip_sports}\n\n"
            f"<b>Sizing</b>\n"
            f"  Estratégia: {cfg.sizing_strategy}\n"
            f"  Copy ratio: {cfg.sizing_params.get('copy_ratio', '?')}x\n\n"
            f"<b>Risco</b>\n"
            f"  Max trade: ${cfg.max_trade_usdc:,.0f}\n"
            f"  Max exposição: ${cfg.max_total_exposure:,.0f}\n"
            f"  Max por mercado: ${cfg.max_per_market:,.0f}\n"
            f"  Max loss diário: ${cfg.max_daily_loss:,.0f}\n"
            f"  Min preço: {cfg.min_price}\n"
            f"  Max preço: {cfg.max_price}"
        )

    # ─── TEXT COMMAND HANDLERS ────────────────────────────────────────

    async def _handle_command(self, command: str):
        """Handle a Telegram text command"""
        cmd = command.split()[0].lower().split("@")[0]
        args = command.split()[1:] if len(command.split()) > 1 else []

        handlers = {
            "/start": self._cmd_dashboard,
            "/dashboard": self._cmd_dashboard,
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/summary": self._cmd_summary,
            "/risk": self._cmd_risk,
            "/wallets": self._cmd_wallets,
            "/halt": self._cmd_halt,
            "/resume": self._cmd_resume,
            "/trades": self._cmd_recent_trades,
            "/pnl": self._cmd_pnl,
            "/settings": self._cmd_settings,
            "/help": self._cmd_help,
        }

        handler = handlers.get(cmd)
        if handler:
            await handler(args)
        else:
            await self.send(f"Comando desconhecido: {cmd}\nUse /help para ver comandos.")

    async def _cmd_dashboard(self, args: list = None):
        if not self._bot_ref:
            return
        status = self._bot_ref.get_status()
        risk = status.get("risk", {})
        daily = risk.get("daily_stats", {})

        halted = risk.get("halted", False)
        state = "🛑 PARADO" if halted else "✅ ATIVO"
        mode = status.get("mode", "?")

        msg = (
            f"🖥 <b>Painel — Copy Trading Bot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Estado: {state}  •  Modo: <b>{mode}</b>\n\n"
            f"💼 Exposição: <b>${risk.get('total_exposure', 0):,.2f}</b>"
            f" / ${risk.get('max_total_exposure', 0):,.0f}\n"
            f"📈 Posições: {risk.get('positions_count', 0)}"
            f"  •  Mercados: {risk.get('markets_count', 0)}\n\n"
            f"<b>Hoje</b>: {daily.get('trades', 0)} trades"
            f"  •  C: ${daily.get('bought_usdc', 0):,.0f}"
            f"  •  V: ${daily.get('sold_usdc', 0):,.0f}\n\n"
            f"⏰ {_now()}"
        )
        await self.send(msg, reply_markup=self._dashboard_kb())

    async def _cmd_status(self, args: list):
        if not self._bot_ref:
            return
        await self.send(self._build_status_text(), reply_markup=self._back_kb())

    async def _cmd_positions(self, args: list):
        if not self._bot_ref:
            return
        kb = self._inline_kb([
            [("🔄 Atualizar", "cb_positions")],
            [("◀️ Voltar", "cb_dashboard")],
        ])
        msg = await self._build_positions_text()
        await self.send(msg, reply_markup=kb)

    async def _cmd_summary(self, args: list):
        await self.send_summary()

    async def _cmd_risk(self, args: list):
        if not self._bot_ref:
            return
        await self.send(self._build_risk_text(), reply_markup=self._back_kb())

    async def _cmd_wallets(self, args: list):
        if not self._bot_ref or not self._bot_ref.watcher:
            return
        await self.send(self._build_wallets_text(), reply_markup=self._back_kb())

    async def _cmd_halt(self, args: list):
        if not self._bot_ref:
            return
        reason = " ".join(args) if args else "Manual halt via Telegram"
        self._bot_ref.risk.halt(reason)
        await self.send(
            f"🛑 Trading halted: {esc(reason)}",
            reply_markup=self._inline_kb([[("▶️ Retomar", "cb_resume")]]),
        )

    async def _cmd_resume(self, args: list):
        if not self._bot_ref:
            return
        self._bot_ref.risk.resume()
        await self.send("✅ Trading resumido!", reply_markup=self._dashboard_kb())

    async def _cmd_recent_trades(self, args: list):
        if not self._bot_ref:
            return
        await self.send(self._build_trades_text(0, 5), reply_markup=self._trades_nav_kb(0, 5))

    async def _cmd_pnl(self, args: list):
        if not self._bot_ref:
            return
        kb = self._inline_kb([
            [("🔄 Atualizar", "cb_pnl"), ("📜 Trades", "cb_trades")],
            [("◀️ Painel", "cb_dashboard")],
        ])
        await self.send(self._build_pnl_text(), reply_markup=kb)

    async def _cmd_settings(self, args: list):
        if not self._bot_ref:
            return
        await self.send(self._build_settings_text(), reply_markup=self._back_kb())

    async def _cmd_help(self, args: list):
        msg = (
            f"🤖 <b>Comandos</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 /status — Status geral do bot\n"
            f"🖥 /dashboard — Painel interativo\n"
            f"🛡 /risk — Exposição e limites\n"
            f"👁 /wallets — Carteiras monitoradas\n"
            f"📜 /trades [N] — Últimos trades\n"
            f"💰 /pnl — Lucro e prejuízo\n"
            f"📋 /summary — Resumo completo\n"
            f"⚙️ /settings — Configurações\n"
            f"🛑 /halt [motivo] — Parar trading\n"
            f"✅ /resume — Retomar trading\n\n"
            f"💡 Todos os comandos também estão\n"
            f"disponíveis no menu lateral (☰)"
        )
        await self.send(msg, reply_markup=self._dashboard_kb())

    async def close(self):
        """Cleanup"""
        await self.stop_polling()
        await self._http.aclose()


# ─── HELPERS ──────────────────────────────────────────────────────────

def esc(text: str) -> str:
    """Escape HTML for Telegram"""
    return html.escape(str(text))


def _now() -> str:
    """Current UTC timestamp string"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _progress_bar(pct: float, width: int = 20) -> str:
    """Generate a text progress bar"""
    filled = int(pct / 100 * width)
    filled = max(0, min(width, filled))
    empty = width - filled
    return "█" * filled + "░" * empty
