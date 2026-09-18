from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.verification import VerificationPrompt
from app.services.turnstile import TURNSTILE_KIND


def render_verification_challenge(prompt: VerificationPrompt, *, retry: bool = False):
    if prompt.kind == TURNSTILE_KIND:
        if not prompt.url:
            raise ValueError("Turnstile prompt is missing its verification URL")
        return (
            "请完成人机验证。",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="开始验证", url=prompt.url)]
                ]
            ),
        )

    builder = InlineKeyboardBuilder()
    for option in prompt.options:
        builder.button(
            text=option.label,
            callback_data=f"verify:{prompt.challenge_id}:{option.token}",
        )
    builder.adjust(3)

    sequence = " → ".join(prompt.remaining_labels)
    if prompt.completed_steps:
        heading = f"已完成 {prompt.completed_steps}/{prompt.total_steps}，继续点击："
    elif retry:
        heading = "顺序不对，请重新点击："
    else:
        heading = "请按顺序点击："
    text = f"{heading}{sequence}\n\n剩余 {prompt.attempts_remaining} 次机会。"
    return text, builder.as_markup()
