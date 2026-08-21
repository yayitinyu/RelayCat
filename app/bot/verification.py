from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.verification import VerificationPrompt


def render_verification_challenge(prompt: VerificationPrompt, *, retry: bool = False):
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
