from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from personal.models import UserProfile

from .models import TelegramBotConfig, TelegramChatLink


class TelegramTestMessageForm(forms.Form):
    bot = forms.ModelChoiceField(
        label=_("Bot"),
        queryset=TelegramBotConfig.objects.none(),
        required=True,
        help_text=_("Selecciona el bot configurado en el Back Office."),
        widget=forms.Select(
            attrs={
                "class": "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm "
                "text-slate-700 shadow-sm focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/70"
            }
        ),
    )
    chat_link = forms.ModelChoiceField(
        label=_("Usuario vinculado"),
        queryset=TelegramChatLink.objects.none(),
        required=True,
        help_text=_("Solo se listan chats verificados asociados al bot seleccionado."),
        widget=forms.Select(
            attrs={
                "class": "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm "
                "text-slate-700 shadow-sm focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/70"
            }
        ),
    )
    text = forms.CharField(
        label=_("Mensaje de prueba"),
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": _("Escribe el mensaje que deseas enviar al chat seleccionado."),
                "class": "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm "
                "text-slate-700 shadow-sm focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/70",
            }
        ),
        max_length=4096,
        required=True,
        help_text=_("El límite estándar de Telegram es de 4096 caracteres por mensaje."),
    )

    def __init__(
        self,
        *args,
        bot_queryset: QuerySet | None = None,
        chat_link_queryset: QuerySet | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        bot_qs = bot_queryset or TelegramBotConfig.objects.filter(is_active=True)
        self.fields["bot"].queryset = bot_qs

        chat_link_qs = chat_link_queryset or TelegramChatLink.objects.filter(
            status=TelegramChatLink.Status.VERIFIED
        )
        self.fields["chat_link"].queryset = chat_link_qs.select_related("user", "bot")

    def clean(self):
        cleaned_data = super().clean()
        bot: TelegramBotConfig | None = cleaned_data.get("bot")
        chat_link: TelegramChatLink | None = cleaned_data.get("chat_link")

        if chat_link and not chat_link.is_verified:
            self.add_error("chat_link", _("El chat seleccionado no está verificado."))

        if bot and chat_link and chat_link.bot_id != bot.id:
            self.add_error("chat_link", _("El chat seleccionado no pertenece al bot indicado."))

        return cleaned_data


class TelegramLinkChatForm(forms.Form):
    bot = forms.ModelChoiceField(
        label=_("Bot"),
        queryset=TelegramBotConfig.objects.none(),
        widget=forms.HiddenInput(),
    )
    update_id = forms.IntegerField(widget=forms.HiddenInput())
    chat_id = forms.IntegerField(widget=forms.HiddenInput())
    telegram_user_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    username = forms.CharField(required=False, widget=forms.HiddenInput())
    first_name = forms.CharField(required=False, widget=forms.HiddenInput())
    last_name = forms.CharField(required=False, widget=forms.HiddenInput())
    language_code = forms.CharField(required=False, widget=forms.HiddenInput())

    user = forms.ModelChoiceField(
        label=_("Colaborador"),
        queryset=UserProfile.objects.none(),
        required=True,
        help_text=_("Selecciona el colaborador con el que se vinculará el chat."),
        widget=forms.Select(
            attrs={
                "class": "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm "
                "text-slate-700 shadow-sm focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/70"
            }
        ),
    )

    auto_verify = forms.BooleanField(
        label=_("Marcar como verificado"),
        required=False,
        initial=True,
        help_text=_("Si está activo, el chat quedará listo para enviar notificaciones de inmediato."),
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-4 w-4 rounded border-slate-300 text-brand focus:ring-brand"
            }
        ),
    )

    def __init__(
        self,
        *args: Any,
        bot_queryset: QuerySet | None = None,
        user_queryset: QuerySet | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        bot_qs = bot_queryset or TelegramBotConfig.objects.filter(is_active=True)
        self.fields["bot"].queryset = bot_qs

        user_qs = user_queryset or UserProfile.objects.filter(is_active=True)
        self.fields["user"].queryset = user_qs.select_related(None).order_by("apellidos", "nombres")


class TelegramVerifyChatForm(forms.Form):
    chat_link = forms.ModelChoiceField(
        label=_("Chat"),
        queryset=TelegramChatLink.objects.none(),
        widget=forms.HiddenInput(),
    )

    def __init__(
        self,
        *args: Any,
        chat_link_queryset: QuerySet | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        qs = chat_link_queryset or TelegramChatLink.objects.all()
        self.fields["chat_link"].queryset = qs
