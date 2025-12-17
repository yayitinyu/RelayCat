import random
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

EMOJIS = ["🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓"]

def generate_verification_challenge():
    """
    Generates a random emoji challenge.
    Returns: (target_emoji, markup)
    """
    target = random.choice(EMOJIS)
    
    # Create a 3x3 grid of random emojis including the target
    options = [target]
    while len(options) < 9:
        e = random.choice(EMOJIS)
        # Allow duplicates to make it slightly harder? or unique?
        # Let's keep it simple, just random filler.
        options.append(e)
    
    random.shuffle(options)
    
    builder = InlineKeyboardBuilder()
    for emoji in options:
        # Callback data format: verify:<emoji>
        builder.button(text=emoji, callback_data=f"verify:{emoji}")
    
    builder.adjust(3)
    return target, builder.as_markup()
