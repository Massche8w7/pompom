def get_player_html(real_link, bot_name):
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>TERABOX PLAYER</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #000000;
            color: #ffffff;
            text-align: center;
            padding: 40px 20px;
            transition: background 0.3s frame, color 0.3s step;
            margin: 0;
        }}
        body.light-mode {{
            background: #ffffff;
            color: #000000;
        }}
        .toggle-btn {{
            background: linear-gradient(135deg, #f1c40f, #f39c12);
            color: #000000;
            border: none;
            padding: 10px 20px;
            font-size: 12px;
            font-weight: 800;
            border-radius: 30px;
            cursor: pointer;
            text-transform: uppercase;
            margin-bottom: 20px;
            box-shadow: 0 4px 15px rgba(241, 196, 15, 0.4);
        }}
        .card {{
            background: rgba(0, 0, 0, 0.4);
            border-radius: 16px;
            padding: 35px;
            max-width: 750px;
            margin: 20px auto;
            position: relative;
            backdrop-filter: blur(25px);
            -webkit-backdrop-filter: blur(25px);
            border: 2px solid transparent;
            background-image: linear-gradient(rgba(0, 0, 0, 0.8), rgba(0, 0, 0, 0.8)), linear-gradient(135deg, #f1c40f, #f39c12);
            background-origin: border-box;
            background-clip: padding-box, border-box;
            box-shadow: 0 20px 50px rgba(241, 196, 15, 0.15), inset 0 1px 20px rgba(255, 255, 255, 0.05);
            transition: all 0.3s;
        }}
        body.light-mode .card {{
            background-image: linear-gradient(rgba(255, 255, 255, 0.9), rgba(255, 255, 255, 0.9)), linear-gradient(135deg, #f1c40f, #f39c12);
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.1);
        }}
        h1 {{
            color: #f1c40f;
            font-size: 24px;
            font-weight: 900;
            letter-spacing: 2px;
            margin: 0;
            text-transform: uppercase;
        }}
        h2 {{
            color: #ffffff;
            font-size: 16px;
            font-weight: 700;
            letter-spacing: 1px;
            margin: 10px 0 25px 0;
            text-transform: uppercase;
        }}
        body.light-mode h2 {{
            color: #000000;
        }}
        p {{
            color: rgba(255, 255, 255, 0.6);
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.5px;
            line-height: 1.6;
            text-transform: uppercase;
            margin-bottom: 25px;
        }}
        body.light-mode p {{
            color: rgba(0, 0, 0, 0.6);
        }}
        video {{
            width: 100%;
            border-radius: 12px;
            background: #000000;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.6);
            border: 1px solid rgba(241, 196, 15, 0.2);
        }}
        .footer {{
            margin-top: 35px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 2px;
            color: #f1c40f;
            text-transform: uppercase;
        }}
    </style>
</head>
<body>
    <button class="toggle-btn" onclick="toggleTheme()">SWITCH THEME</button>
    <div class="card">
        <h1>{bot_name.upper()}</h1>
        <h2>SAMRABOTZ</h2>
        <p>YOUR LINK STREAMING PORTAL IS COMPLETELY MOUNTED INSIDE THIS SECURE WRAPPER FRAME NODE.</p>
        <video controls autoplay preload="auto">
            <source src="{real_link}" type="video/mp4">
            YOUR BROWSER DOES NOT SUPPORT THE VIDEO PLAYBACK MATRIX.
        </video>
        <div class="footer">SAMRABOTZ COPYRIGHT</div>
    </div>
    <script>
        function toggleTheme() {{
            document.body.classList.toggle('light-mode');
        }}
    </script>
</body>
</html>"""
