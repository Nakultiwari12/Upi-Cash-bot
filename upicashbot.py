async def approve_withdraw_cmd(message):
    user_id = message.from_user.id
    amount = float(message.text.split()[1])

    # Check the user's current balance
    current_balance = await get_user_balance(user_id)
    if current_balance < amount:
        await message.reply("Error: Insufficient funds.")
        return

    # Proceed to subtract and approve the withdrawal
    await subtract_amount(user_id, amount)
    await message.reply("Withdrawal approved.")