@echo off
title GoFood Scraper by Askhab Firdaus 
color 0B

echo =======================================================
echo                 GOFOOD SCRAPER BOT UI
echo           GitHub: askab67/gofood-scraper
echo =======================================================
echo.

:: Memastikan semua library terinstal (termasuk Streamlit)
echo [INFO] Memeriksa dan menginstal library yang dibutuhkan (mohon tunggu sebentar)...
pip install -q -r requirements.txt

echo.
echo [INFO] Library siap! Membuka antarmuka Streamlit...
echo [INFO] Jangan tutup jendela hitam ini selama aplikasi berjalan.
echo.

:: Menggunakan "python -m streamlit" lebih kebal dari error PATH di Windows
python -m streamlit run app.py

echo.
pause