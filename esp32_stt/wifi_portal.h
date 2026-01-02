#ifndef WIFI_PORTAL_H
#define WIFI_PORTAL_H

// Captive Portal HTML Page
const char PORTAL_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Magoo Setup</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            padding: 30px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 10px;
            font-size: 24px;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            color: #555;
            margin-bottom: 8px;
            font-weight: 500;
            font-size: 14px;
        }
        input {
            width: 100%;
            padding: 14px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        }
        .scan-btn {
            background: #f0f0f0;
            color: #333;
            margin-bottom: 15px;
        }
        .scan-btn:hover {
            background: #e0e0e0;
            box-shadow: none;
            transform: none;
        }
        .networks {
            max-height: 200px;
            overflow-y: auto;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            margin-bottom: 15px;
            display: none;
        }
        .network-item {
            padding: 12px 14px;
            border-bottom: 1px solid #eee;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
        }
        .network-item:hover { background: #f5f5f5; }
        .network-item:last-child { border-bottom: none; }
        .signal { color: #999; font-size: 12px; }
        .success {
            background: #4CAF50;
            color: white;
            padding: 15px;
            border-radius: 10px;
            text-align: center;
            margin-top: 20px;
            display: none;
        }
        .error {
            background: #f44336;
            color: white;
            padding: 15px;
            border-radius: 10px;
            text-align: center;
            margin-top: 20px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Magoo</h1>
        <p class="subtitle">WiFi設定</p>
        
        <form id="configForm">
            <button type="button" class="scan-btn" onclick="scanNetworks()">WiFiをスキャン</button>
            
            <div id="networks" class="networks"></div>
            
            <div class="form-group">
                <label>WiFi SSID</label>
                <input type="text" name="ssid" id="ssid" placeholder="WiFiネットワーク名" required>
            </div>
            
            <div class="form-group">
                <label>WiFi パスワード</label>
                <input type="password" name="password" id="password" placeholder="パスワード">
            </div>
            
            <button type="submit">接続</button>
        </form>
        
        <div id="success" class="success">✅ 接続中...再起動します</div>
        <div id="error" class="error">❌ エラー</div>
    </div>
    
    <script>
        function scanNetworks() {
            const btn = document.querySelector('.scan-btn');
            const networksDiv = document.getElementById('networks');
            btn.textContent = 'スキャン中...';
            btn.disabled = true;
            
            fetch('/scan')
                .then(response => response.json())
                .then(data => {
                    networksDiv.innerHTML = '';
                    networksDiv.style.display = 'block';
                    
                    if (data.networks && data.networks.length > 0) {
                        data.networks.forEach(network => {
                            const item = document.createElement('div');
                            item.className = 'network-item';
                            item.innerHTML = '<span>' + network.ssid + '</span><span class="signal">' + network.rssi + ' dBm</span>';
                            item.onclick = () => document.getElementById('ssid').value = network.ssid;
                            networksDiv.appendChild(item);
                        });
                    } else {
                        networksDiv.innerHTML = '<div class="network-item">見つかりません</div>';
                    }
                    btn.textContent = 'WiFiをスキャン';
                    btn.disabled = false;
                })
                .catch(err => {
                    btn.textContent = 'WiFiをスキャン';
                    btn.disabled = false;
                });
        }
        
        document.getElementById('configForm').onsubmit = function(e) {
            e.preventDefault();
            fetch('/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    ssid: document.getElementById('ssid').value,
                    password: document.getElementById('password').value
                })
            })
            .then(response => response.json())
            .then(result => {
                if (result.success) {
                    document.getElementById('success').style.display = 'block';
                    document.getElementById('error').style.display = 'none';
                } else {
                    document.getElementById('error').textContent = '❌ ' + (result.message || 'エラー');
                    document.getElementById('error').style.display = 'block';
                }
            })
            .catch(() => document.getElementById('error').style.display = 'block');
        };
    </script>
</body>
</html>
)rawliteral";

#endif
