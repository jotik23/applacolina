from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View

from notifications.forms import (
    TelegramLinkChatForm,
    TelegramTestMessageForm,
    TelegramVerifyChatForm,
)
from notifications.models import (
    NotificationChannel,
    NotificationDispatch,
    NotificationEvent,
    NotificationTopic,
    TelegramBotConfig,
    TelegramChatLink,
    TelegramInboundUpdate,
)
from notifications.services.telegram import TelegramNotificationError, TelegramNotificationSender
from personal.models import UserProfile


MANUAL_TOPIC_SLUG = "telegram_manual_test_message"


@dataclass(slots=True)
class UpdateSummary:
    update: TelegramInboundUpdate
    update_type: str
    chat_id: int | None
    from_name: str
    username: str
    text_preview: str
    raw_payload: dict[str, Any]
    payload_json: str
    telegram_user_id: int | None
    first_name: str
    last_name: str
    language_code: str
    chat_link: TelegramChatLink | None = None
    is_linked: bool = False
    is_verified: bool = False
    link_form: TelegramLinkChatForm | None = None
    verify_form: TelegramVerifyChatForm | None = None


class TelegramUpdatesView(UserPassesTestMixin, LoginRequiredMixin, View):
    template_name = "notifications/telegram_updates.html"
    test_form_class = TelegramTestMessageForm
    link_form_class = TelegramLinkChatForm
    verify_form_class = TelegramVerifyChatForm
    updates_limit = 25
    raise_exception = True

    def test_func(self) -> bool:
        user = self.request.user
        return bool(getattr(user, "is_staff", False))

    def get(self, request: HttpRequest) -> HttpResponse:
        context = self._build_context(request)
        return render(request, self.template_name, context)

    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action") or "send"
        if action == "link":
            return self._handle_link(request)
        if action == "verify":
            return self._handle_verify(request)
        return self._handle_send(request)

    def _handle_send(self, request: HttpRequest) -> HttpResponse:
        context = self._build_context(request, post_data=request.POST, action="send")
        form: TelegramTestMessageForm = context["test_form"]
        selected_bot: TelegramBotConfig | None = context["selected_bot"]

        if selected_bot is None:
            messages.error(request, _("Para enviar mensajes es necesario tener un bot activo configurado."))
            return render(request, self.template_name, context)

        if form.is_valid():
            try:
                self._send_test_message(request, form.cleaned_data)
            except TelegramNotificationError as exc:
                messages.error(
                    request,
                    _("No se pudo enviar el mensaje de prueba. Detalle: %(error)s") % {"error": str(exc)},
                )
            else:
                messages.success(request, _("Mensaje enviado correctamente."))
                return redirect(self._success_url(request, selected_bot))

        return render(request, self.template_name, context)

    def _handle_link(self, request: HttpRequest) -> HttpResponse:
        bot_queryset = self._get_bot_queryset()
        user_queryset = self._get_user_queryset()
        link_form = self.link_form_class(
            request.POST,
            bot_queryset=bot_queryset,
            user_queryset=user_queryset,
        )

        selected_bot = None
        if link_form.is_valid():
            selected_bot = link_form.cleaned_data["bot"]
            try:
                chat_link = self._link_chat(request, link_form)
            except ValidationError as exc:
                link_form.add_error(None, exc.message)
            else:
                chat_label = self._chat_label(chat_link.chat_id, chat_link.username)
                user_label = chat_link.user.get_full_name() or str(chat_link.user)
                if chat_link.is_verified:
                    messages.success(
                        request,
                        _("Chat %(chat)s enlazado y verificado para %(user)s.") % {
                            "chat": chat_label,
                            "user": user_label,
                        },
                    )
                else:
                    messages.success(
                        request,
                        _("Chat %(chat)s enlazado correctamente para %(user)s.") % {
                            "chat": chat_label,
                            "user": user_label,
                        },
                    )
                return redirect(self._success_url(request, chat_link.bot))
        else:
            selected_bot = self._resolve_bot_from_data(link_form.data)

        context = self._build_context(
            request,
            post_data=request.POST,
            action="link",
            selected_bot_override=selected_bot,
        )
        self._inject_link_form(context, link_form)
        return render(request, self.template_name, context)

    def _handle_verify(self, request: HttpRequest) -> HttpResponse:
        verify_form = self.verify_form_class(
            request.POST,
            chat_link_queryset=self._get_chat_link_queryset(None),
        )

        selected_bot = None
        if verify_form.is_valid():
            chat_link: TelegramChatLink = verify_form.cleaned_data["chat_link"]
            selected_bot = chat_link.bot
            self._verify_chat(chat_link)
            chat_label = self._chat_label(chat_link.chat_id, chat_link.username)
            user_label = chat_link.user.get_full_name() or str(chat_link.user)
            messages.success(
                request,
                _("Chat %(chat)s verificado para %(user)s.") % {
                    "chat": chat_label,
                    "user": user_label,
                },
            )
            return redirect(self._success_url(request, chat_link.bot))
        else:
            selected_bot = self._resolve_bot_from_chat_link_id(request.POST.get("chat_link"))
            if selected_bot is None:
                selected_bot = self._resolve_bot_from_data(request.POST)

        context = self._build_context(
            request,
            post_data=request.POST,
            action="verify",
            selected_bot_override=selected_bot,
        )
        self._inject_verify_form(context, verify_form)
        return render(request, self.template_name, context)

    def _build_context(
        self,
        request: HttpRequest,
        *,
        post_data: dict[str, Any] | None = None,
        action: str | None = None,
        selected_bot_override: TelegramBotConfig | None = None,
    ) -> dict[str, Any]:
        bot_queryset = self._get_bot_queryset()
        bots = list(bot_queryset)

        selected_bot = selected_bot_override or self._resolve_selected_bot(request, bots, post_data)
        user_queryset = self._get_user_queryset()
        chat_link_qs = self._get_chat_link_queryset(selected_bot)

        chat_links = list(chat_link_qs.select_related("user"))
        verified_chat_links = [link for link in chat_links if link.is_verified]
        pending_chat_links = [link for link in chat_links if not link.is_verified]

        verified_qs = (
            chat_link_qs.filter(status=TelegramChatLink.Status.VERIFIED)
            if selected_bot
            else TelegramChatLink.objects.none()
        )

        if post_data is not None and action == "send":
            test_form = self.test_form_class(
                post_data,
                bot_queryset=bot_queryset,
                chat_link_queryset=verified_qs,
            )
        else:
            initial: dict[str, Any] = {}
            if selected_bot:
                initial["bot"] = selected_bot
            test_form = self.test_form_class(
                initial=initial,
                bot_queryset=bot_queryset,
                chat_link_queryset=verified_qs,
            )

        updates = self._fetch_updates(selected_bot)
        chat_link_by_chat_id = {link.chat_id: link for link in chat_links if link.chat_id}

        for summary in updates:
            summary.chat_link = chat_link_by_chat_id.get(summary.chat_id)
            summary.is_linked = summary.chat_link is not None
            summary.is_verified = summary.chat_link.is_verified if summary.chat_link else False

            if selected_bot and summary.chat_id and not summary.is_linked:
                summary.link_form = self.link_form_class(
                    initial={
                        "bot": selected_bot,
                        "update_id": summary.update.pk,
                        "chat_id": summary.chat_id,
                        "telegram_user_id": summary.telegram_user_id or "",
                        "username": summary.username,
                        "first_name": summary.first_name,
                        "last_name": summary.last_name,
                        "language_code": summary.language_code,
                    },
                    bot_queryset=bot_queryset,
                    user_queryset=user_queryset,
                )

            if summary.chat_link and not summary.is_verified:
                summary.verify_form = self.verify_form_class(
                    initial={"chat_link": summary.chat_link},
                    chat_link_queryset=chat_link_qs,
                )

        pending_chat_cards = [
            {
                "link": link,
                "verify_form": self.verify_form_class(
                    initial={"chat_link": link},
                    chat_link_queryset=chat_link_qs,
                ),
            }
            for link in pending_chat_links
        ]

        context = {
            "test_form": test_form,
            "selected_bot": selected_bot,
            "bots": bots,
            "updates": updates,
            "verified_chat_links": verified_chat_links,
            "pending_chat_cards": pending_chat_cards,
            "linkable_update_count": sum(1 for summary in updates if summary.link_form),
            "pending_verification_count": len(pending_chat_links),
        }
        return context

    def _resolve_selected_bot(
        self,
        request: HttpRequest,
        bots: list[TelegramBotConfig],
        post_data: dict[str, Any] | None,
    ) -> TelegramBotConfig | None:
        bot_id: str | None = None
        if post_data:
            bot_id = post_data.get("bot") or None
        if not bot_id:
            bot_id = request.GET.get("bot")

        if bot_id:
            for bot in bots:
                if str(bot.pk) == str(bot_id):
                    return bot

        return bots[0] if bots else None

    def _fetch_updates(self, selected_bot: TelegramBotConfig | None) -> list[UpdateSummary]:
        queryset = TelegramInboundUpdate.objects.all().select_related("bot").order_by("-received_at")
        if selected_bot:
            queryset = queryset.filter(bot=selected_bot)
        updates = list(queryset[: self.updates_limit])
        return [self._build_update_summary(update) for update in updates]

    def _build_update_summary(self, update: TelegramInboundUpdate) -> UpdateSummary:
        payload = update.payload or {}
        block, key = self._extract_primary_block(payload)

        from_block: dict[str, Any] = {}
        chat_block: dict[str, Any] = {}
        text_preview = ""
        chat_id: int | None = None
        telegram_user_id: int | None = None
        first_name = ""
        last_name = ""
        language_code = ""

        if key == "callback_query":
            callback = payload.get("callback_query") or {}
            from_block = callback.get("from") or {}
            chat_block = callback.get("message", {}).get("chat") or {}
            chat_id = chat_block.get("id")
            text_preview = callback.get("data") or callback.get("message", {}).get("text") or ""
        elif key == "inline_query":
            inline_query = payload.get("inline_query") or {}
            from_block = inline_query.get("from") or {}
            text_preview = inline_query.get("query") or ""
        elif block:
            from_block = block.get("from") or block.get("user") or {}
            chat_block = block.get("chat") or {}
            chat_id = chat_block.get("id")
            text_preview = block.get("text") or block.get("caption") or ""
            if not text_preview and key in {"my_chat_member", "chat_member"}:
                new_status = block.get("new_chat_member", {}).get("status")
                old_status = block.get("old_chat_member", {}).get("status")
                if new_status and old_status:
                    text_preview = _("Cambio de estado: %(old)s → %(new)s") % {
                        "old": old_status,
                        "new": new_status,
                    }
                elif new_status:
                    text_preview = _("Nuevo estado: %(status)s") % {"status": new_status}

        telegram_user_id = from_block.get("id")
        first_name = from_block.get("first_name") or ""
        last_name = from_block.get("last_name") or ""
        language_code = from_block.get("language_code") or ""

        username = from_block.get("username") or ""
        full_name = " ".join(filter(None, [first_name, last_name])).strip()
        if not full_name:
            full_name = from_block.get("name") or ""
        if not full_name and username:
            full_name = f"@{username}"

        if not text_preview:
            text_preview = payload.get("inline_query", {}).get("query", "")
        if not text_preview:
            text_preview = payload.get("message", {}).get("text", "")
        if not text_preview:
            text_preview = ""

        if len(text_preview) > 180:
            text_preview = text_preview[:177].rstrip() + "…"

        return UpdateSummary(
            update=update,
            update_type=key or "unknown",
            chat_id=chat_id,
            from_name=full_name,
            username=username,
            text_preview=text_preview,
            raw_payload=payload,
            payload_json=json.dumps(payload, indent=2, sort_keys=True),
            telegram_user_id=telegram_user_id,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
        )

    @staticmethod
    def _extract_primary_block(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        candidate_keys = (
            "message",
            "edited_message",
            "callback_query",
            "my_chat_member",
            "chat_member",
            "channel_post",
            "edited_channel_post",
            "inline_query",
        )
        for key in candidate_keys:
            block = payload.get(key)
            if block:
                return block, key
        return None, None

    def _send_test_message(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> None:
        bot: TelegramBotConfig = cleaned_data["bot"]
        chat_link: TelegramChatLink = cleaned_data["chat_link"]
        text: str = cleaned_data["text"]

        topic, _ = NotificationTopic.objects.get_or_create(
            slug=MANUAL_TOPIC_SLUG,
            defaults={
                "name": _("Mensajes manuales de Telegram"),
                "description": _("Mensajes generados manualmente desde el panel de monitoreo."),
                "default_channel": NotificationChannel.TELEGRAM,
            },
        )

        with transaction.atomic():
            event = NotificationEvent.objects.create(
                topic=topic,
                triggered_by=request.user if isinstance(request.user, UserProfile) else None,
                source_app=NotificationEvent.SourceApp.MANUAL,
                source_identifier=f"telegram-chat-link:{chat_link.pk}",
                context={
                    "chat_link_id": chat_link.pk,
                    "bot_id": bot.pk,
                    "requested_by": request.user.get_full_name()
                    if hasattr(request.user, "get_full_name")
                    else request.user.get_username(),
                    "message": text,
                },
            )

            dispatch = NotificationDispatch.objects.create(
                event=event,
                chat_link=chat_link,
                channel=NotificationChannel.TELEGRAM,
                payload={"text": text},
            )

        sender = TelegramNotificationSender()
        sender.send_dispatch(dispatch)

    def _success_url(self, request: HttpRequest, bot: TelegramBotConfig) -> str:
        return f"{reverse('notifications:telegram-updates')}?bot={bot.pk}"

    def _link_chat(self, request: HttpRequest, form: TelegramLinkChatForm) -> TelegramChatLink:
        bot: TelegramBotConfig = form.cleaned_data["bot"]
        update_id: int = form.cleaned_data["update_id"]

        try:
            update = TelegramInboundUpdate.objects.select_for_update().get(pk=update_id, bot=bot)
        except TelegramInboundUpdate.DoesNotExist as exc:
            raise ValidationError(_("El update seleccionado no pertenece al bot activo.")) from exc

        chat_id = form.cleaned_data["chat_id"]
        telegram_user_id = form.cleaned_data["telegram_user_id"]
        username = form.cleaned_data["username"] or ""
        first_name = form.cleaned_data["first_name"] or ""
        last_name = form.cleaned_data["last_name"] or ""
        language_code = form.cleaned_data["language_code"] or ""
        user: UserProfile = form.cleaned_data["user"]
        auto_verify: bool = form.cleaned_data["auto_verify"]

        now = timezone.now()

        try:
            with transaction.atomic():
                link, created = TelegramChatLink.objects.select_for_update().get_or_create(
                    bot=bot,
                    chat_id=chat_id,
                    defaults={
                        "user": user,
                        "telegram_user_id": telegram_user_id,
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "language_code": language_code,
                        "status": TelegramChatLink.Status.PENDING,
                        "last_interaction_at": update.received_at,
                    },
                )

                link.user = user
                link.telegram_user_id = telegram_user_id
                link.username = username
                link.first_name = first_name
                link.last_name = last_name
                link.language_code = language_code
                link.last_interaction_at = update.received_at or now

                update_fields = [
                    "user",
                    "telegram_user_id",
                    "username",
                    "first_name",
                    "last_name",
                    "language_code",
                    "last_interaction_at",
                    "updated_at",
                ]

                if auto_verify:
                    link.status = TelegramChatLink.Status.VERIFIED
                    link.verified_at = now
                    update_fields.extend(["status", "verified_at"])
                elif created:
                    link.status = TelegramChatLink.Status.PENDING
                    update_fields.append("status")

                link.save(update_fields=update_fields)

                update.processed_at = now
                update.processing_error = ""
                update.save(update_fields=("processed_at", "processing_error"))
        except IntegrityError as exc:
            raise ValidationError(
                _("El colaborador seleccionado ya cuenta con un chat enlazado para este bot.")
            ) from exc

        return link

    def _verify_chat(self, chat_link: TelegramChatLink) -> TelegramChatLink:
        now = timezone.now()
        chat_link.status = TelegramChatLink.Status.VERIFIED
        chat_link.verified_at = now
        chat_link.last_interaction_at = now
        chat_link.save(update_fields=("status", "verified_at", "last_interaction_at", "updated_at"))
        return chat_link

    def _get_bot_queryset(self):
        return TelegramBotConfig.objects.filter(is_active=True).order_by("name")

    def _get_user_queryset(self):
        return UserProfile.objects.filter(is_active=True).order_by("apellidos", "nombres")

    def _get_chat_link_queryset(self, bot: TelegramBotConfig | None):
        qs = TelegramChatLink.objects.all()
        if bot is not None:
            qs = qs.filter(bot=bot)
        return qs

    def _inject_link_form(self, context: dict[str, Any], link_form: TelegramLinkChatForm) -> None:
        target_update_id = link_form.data.get("update_id")
        if not target_update_id:
            return
        for summary in context.get("updates", []):
            if str(summary.update.pk) == str(target_update_id):
                summary.link_form = link_form
                break

    def _inject_verify_form(self, context: dict[str, Any], verify_form: TelegramVerifyChatForm) -> None:
        target_chat_link_id = verify_form.data.get("chat_link")
        if not target_chat_link_id:
            return
        for summary in context.get("updates", []):
            if summary.chat_link and str(summary.chat_link.pk) == str(target_chat_link_id):
                summary.verify_form = verify_form
        for card in context.get("pending_chat_cards", []):
            link = card.get("link")
            if link and str(link.pk) == str(target_chat_link_id):
                card["verify_form"] = verify_form

    def _resolve_bot_from_data(self, data: Any) -> TelegramBotConfig | None:
        bot_id = data.get("bot") if hasattr(data, "get") else None
        if not bot_id:
            return None
        try:
            return TelegramBotConfig.objects.get(pk=bot_id, is_active=True)
        except TelegramBotConfig.DoesNotExist:
            return None

    def _resolve_bot_from_chat_link_id(self, chat_link_id: Any) -> TelegramBotConfig | None:
        if not chat_link_id:
            return None
        try:
            chat_link = TelegramChatLink.objects.select_related("bot").get(pk=chat_link_id)
        except TelegramChatLink.DoesNotExist:
            return None
        return chat_link.bot if chat_link.bot.is_active else None

    @staticmethod
    def _chat_label(chat_id: int | None, username: str | None) -> str:
        if username:
            return f"@{username}"
        if chat_id is not None:
            return f"ID {chat_id}"
        return _("chat sin identificador")
