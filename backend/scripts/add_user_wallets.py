#!/usr/bin/env python3
import json
import os
import re
import sys
from datetime import datetime, timezone

# Tambahkan backend ke sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.infrastructure.database.session import SessionLocal
from app.infrastructure.database.models import WatchlistWalletORM
from app.core.config import settings

USER_INPUT_TEXT = """
7RMJ1wnuhry4Ue8ZL9LrDykTtREdUzrhoH4pUG6W11Md

4Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t

6SGaqjFTkHgrs566DAeZ5an4B8Nyws6nHwoQQtY5vPhK

GjFPEFQmyKxAUhwekRNzQyjLPr6fwQMAmmJtA9Xceu9E

2LVJPXfs3U9bTnXX1y1vQdYAU9ZC7URTchNzDAdu4Y1q

CLM6E4zpTviEC77nWKogpVLQoXx9tgoQCYJ8NibxKg1Q

Cci6qyFyfV7aeHUkyyRUc16DoxQfCH2k7D9Zx1AeC5eb

4gisgHici59DGgwDX4PVZDSdiG4Hyus93H1GqewCiDNZVaKw5oawBGVTyXv6KvFaCKXwaAAQFWnFXScbBNouVJ1C

5kNgeiSkaaLFN39HARtySDPhVdB1G6Hx6XERe5KxqTwJmc5dH8FnF25nsfeUxBQpF34SU3LxZc7tiyTpN5kqo419

2gV4gNiBw73QkeMRZbz6rfW4Dem5Tn9QwiZUpi59p5RHBYKoKpT2nNgieJF5LUEdAdHKgiamrBp4rJm8fhWAasYV

5bNjmBmRAhv9fxb9VKpo95gAiUcbfL5EVnBhK1gFpzPjYKvHvE5iY5gwqJyivj3BVC87Gn1LcmAXuYph976oE6pZ

MC6YN9Tr3xrHVLo4q2SWi4DqTojjJNQ2RZJk6LX9osgSJWgtHS964kfbqyEKgnrNnPYTRy99z5TwPDfoRMm6VJo

dJsLVnzi7rrSpqiubL64FP7t8k9SYfoog57RJA1zFehW3RfXSHM1yeHcpB1Fjj3EyZansZW1moeGo7KPQZjL7kS

dJsLVnzi7rrSpqiubL64FP7t8k9SYfoog57RJA1zFehW3RfXSHM1yeHcpB1Fjj3EyZansZW1moeGo7KPQZjL7kS


zW7NNx9u5LfH4v2uaQadSRkfEyJwB3gr1bvrFA9MBDRr1FnbpdiWKcKuuZJnZRy499jAEusmY7Xi3dFDAjDcWUX

4LFX4bF3xzNq6XyijHjRcWEHfTgDtAJa4vDYABvN9BNqoegCdVbxtcE9zWcwgoBpfsznGX3M7otUg7U7YLgaz9di

55t5es4h71wtJF3niCiEen1ThP5b4EysmJF6VXDFJCYyeLFxDVGk6qAgqNDVUdGnsBdgfQsso4aVRmgr6bZp9mjf

9dJKzPJQVoSLQ3ujdzUpUw3o2Ef2kTJLTgbLWnCMMD3i

36HEQ97YPqNc2t3f85vTVD83qZP5dcZ8m3b4AYCK9nka1Z5FNt7NXZNuwxijvEi8Uakp4SchMavWWGnnGF5kMtVr

5uSNZfK1eLk9j6gR9jhYcfbHd4XtpgHnZP79fVMUcKQH

FGT2KNcfN5UpMt84MQWzaeajbRZ2RwgcboJpL18asrqR

7qWNvn9bDcMrY4VUxAacrLw5nSKoabHNUKWZ6ojow5FK

DxM1hfY8FQ8dNGrucuJzhJcF8KRbjk8WBwrgKvQ9spPv

GjtFLFHhpHxPpaLhi6mEYXW9AN6tFkXQq56e4E9g2oB7

DgKeRrDYYSvhfG8FjgSPGpwGgw4YkaDzyByJGDPNhbQm

12EgDiLpbh1eZVoz7SwWtLQc6YcNYnvFQxxq1KpYXbxw

H6VeEKXJnJBrdUqhfmCEr5ZVVsiHPpw4touMox2mqTqW

H6VeEKXJnJBrdUqhfmCEr5ZVVsiHPpw4touMox2mqTqW

25gvYDM18e1QAKXXG17isLxeGjM9VWZFqm7Dgcr9YiEf

4Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t

E9x69qCVeiHHdcEb7GyNEChkstdCovR291ouQZJM4yiJ

9dJKzPJQVoSLQ3ujdzUpUw3o2Ef2kTJLTgbLWnCMMD3i

BJwRYVBpRuxvPsbCPQkxnxS2Zr797LQSta4uDkgX7X7x

67izjoWrKUyJAePtegzUPCJJR6N5X1PLcvH5PKRYByDz

CUteGF9BftjWexNsKgNT69J6CG8VHDQQioN69iwRyetX

FuWvbBBYU8LZyisSdRNuwYMkUE3x2P88u33eaGMwZ3B7

F87HCE7xHd2H8TeiZhZRif43WcwuA1ULmjKskmVCvHD2

4EsYuWFZAt1PfNJq8Jr7monip43gNqrQ7k2Kne1npqJx

A6uQZGNjpxr2PhrFSTQAw1npKw98XZKvcVCJiHD3icCH

Cqgifaf7CCRsMqzi941fWXt8RdNN51QgGX1PycRudGbc

Eu2LzFxVUV4syLeakdT1Ex1QUwgCSPAYG4andkYR2h55

BC5vZh37bTDE4Dow6ZPtG5Pm2Y2VkvevcvZMwEt9Wa74

6Tdk5Zw6mCieZMjwL1BzaYtpfpSVQQf3PTpUYmDS2HMg

4TuRFzZjjvhMdeK6DWAZcHksFPTFBEu7SJgNMUg57kjJ

8kspuMffnBGzpGkpGFArAApQRmGM5deZFEf7Y9TJ8vXd

HNRqcUosjddSsbwYpv3eLtGn9hBfCSnNR8CAXgY8d81S

E66MpHEb946SM3iZyvpbQhm4q1vKwT3DHbt3AvGKqVH5

7mNxxntrwguaSrpoJFGFccrxF5T15NBS6MZe6swgUaH7

E3LSQrXmmdwnGyaJawSszKpjYS3dD5VjyWyyuTNhywRG

AeuLW8uUqdxBEKGTdyNHLqRt8Wr4Mem1o2Bhxt1kaXiN

51ABa6GRidLfBTN1iniSbVtLRcGbgXzLci9BMauFDsuA

A6p1dFzq1sqiVeVmx62nQmvBzi1CYN4pH4MChL1eHSBz
"""

BASE58_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")

def clean_and_split_tokens(text: str) -> list:
    raw_tokens = [t.strip() for t in text.split() if t.strip()]
    addresses = []
    
    for token in raw_tokens:
        # Check for concatenations of length 86-88
        if 86 <= len(token) <= 88:
            part1 = token[:44]
            part2 = token[44:]
            if BASE58_PATTERN.match(part1):
                addresses.append(part1)
            if BASE58_PATTERN.match(part2):
                addresses.append(part2)
        elif 32 <= len(token) <= 44:
            if BASE58_PATTERN.match(token):
                addresses.append(token)
    return list(dict.fromkeys(addresses)) # remove duplicates keeping order

def main():
    print("=" * 60)
    print("REGISTERING USER PROVIDED SMART WALLETS")
    print("=" * 60)
    
    new_addresses = clean_and_split_tokens(USER_INPUT_TEXT)
    print(f"Parsed {len(new_addresses)} unique wallets from input.")
    
    # 1. Add to SQLite watchlist_wallets database table
    db = SessionLocal()
    added_db_count = 0
    skipped_db_count = 0
    
    try:
        for idx, addr in enumerate(new_addresses):
            existing = db.query(WatchlistWalletORM).filter(
                WatchlistWalletORM.wallet_address == addr
            ).first()
            
            if existing:
                skipped_db_count += 1
                continue
                
            new_wallet = WatchlistWalletORM(
                wallet_address=addr,
                label=f"User Added Smart Wallet #{idx+1} ({addr[:6]})",
                source="manual",
                active=True,
                status="approved",
                added_at=datetime.now(timezone.utc)
            )
            db.add(new_wallet)
            added_db_count += 1
            
        db.commit()
        print(f"  -> SQLite DB: {added_db_count} new wallets added, {skipped_db_count} skipped (already exist).")
    except Exception as e:
        db.rollback()
        print(f"Failed updating DB: {e}")
        return
    finally:
        db.close()
        
    # 2. Update backend/.env TARGET_WALLETS array
    env_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_file):
        print("env file not found. Skipping env update.")
        return
        
    with open(env_file, "r") as f:
        env_content = f.read()
        
    # Find existing TARGET_WALLETS line
    match = re.search(r"TARGET_WALLETS='(\[.*?\])'", env_content)
    if not match:
        # try without quotes
        match = re.search(r"TARGET_WALLETS=(\[.*?\])", env_content)
        
    if not match:
        print("Could not find TARGET_WALLETS variable in .env.")
        return
        
    try:
        existing_wallets_str = match.group(1)
        existing_list = json.loads(existing_wallets_str)
    except Exception as e:
        print(f"Could not parse existing TARGET_WALLETS from .env: {e}")
        return
        
    # Combine lists while maintaining uniqueness
    combined_list = list(existing_list)
    added_env_count = 0
    for addr in new_addresses:
        if addr not in combined_list:
            combined_list.append(addr)
            added_env_count += 1
            
    # Replace in .env string
    new_wallets_str = json.dumps(combined_list)
    new_line = f"TARGET_WALLETS='{new_wallets_str}'"
    
    # Do replacement
    updated_content = env_content.replace(match.group(0), new_line)
    
    with open(env_file, "w") as f:
        f.write(updated_content)
        
    print(f"  -> .env configuration: Added {added_env_count} new wallets to TARGET_WALLETS list.")
    print("SUCCESSFULLY ADDED NEW WALLETS TO BOTH DB AND CONFIG!")

if __name__ == "__main__":
    main()
