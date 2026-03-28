"""
Diagnostic script to check backend networking and fix Android emulator connection.
Run this on your PC where the backend is running.
"""
import socket
import subprocess
import sys
from pathlib import Path

def check_port_listening():
    """Check if port 8000 is listening and on which interface"""
    print("\n" + "="*60)
    print("CHECKING PORT 8000 LISTENING STATUS")
    print("="*60)
    
    try:
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        for line in result.stdout.split('\n'):
            if ':8000' in line:
                print(f"✓ Found: {line}")
                # Check if it's listening on all interfaces (0.0.0.0) or just localhost
                if '0.0.0.0:8000' in line:
                    print("\n✓ GOOD: Backend listening on 0.0.0.0:8000 (all interfaces)")
                    print("  Android emulator should be able to reach it via 10.0.2.2:8000")
                    return True
                elif '127.0.0.1:8000' in line or 'LOCALHOST:8000' in line:
                    print("\n✗ PROBLEM: Backend listening on 127.0.0.1:8000 (localhost only)")
                    print("  Android emulator CANNOT reach it via 10.0.2.2:8000")
                    return False
        
        print("✗ Port 8000 not found in listening ports")
        return False
        
    except Exception as e:
        print(f"Error checking ports: {e}")
        return False

def check_firewall():
    """Check Windows Firewall for port 8000"""
    print("\n" + "="*60)
    print("CHECKING WINDOWS FIREWALL")
    print("="*60)
    
    try:
        result = subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=all'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if 'FastAPI Backend 8000' in result.stdout or '8000' in result.stdout:
            print("✓ Port 8000 found in firewall rules")
            return True
        else:
            print("⚠ Port 8000 not explicitly allowed in firewall")
            print("  (This might still work if Windows Firewall allows by default)")
            return None
            
    except Exception as e:
        print(f"Error checking firewall: {e}")
        return None

def get_pc_ip():
    """Get PC's IP address visible to network"""
    print("\n" + "="*60)
    print("YOUR PC'S IP ADDRESSES")
    print("="*60)
    
    try:
        result = subprocess.run(
            ['ipconfig'],
            capture_output=True,
            text=True
        )
        
        ips = []
        for line in result.stdout.split('\n'):
            if 'IPv4 Address' in line and ':' in line:
                ip = line.split(':')[1].strip()
                if ip and ip != '127.0.0.1':
                    ips.append(ip)
                    print(f"  → {ip}")
        
        if ips:
            print(f"\nFor physical Android device, use: http://{ips[0]}:8000")
            return ips[0]
        return None
        
    except Exception as e:
        print(f"Error getting IP: {e}")
        return None

def main():
    print("\n" + "="*60)
    print("ANDROID EMULATOR NETWORKING DIAGNOSTIC")
    print("="*60)
    
    listening_ok = check_port_listening()
    firewall_ok = check_firewall()
    pc_ip = get_pc_ip()
    
    # Recommendations
    print("\n" + "="*60)
    print("RECOMMENDATIONS")
    print("="*60)
    
    if not listening_ok:
        print("\n⚠ ISSUE FOUND: Backend not listening on 0.0.0.0")
        print("\nSOLUTION:")
        print("  Option 1 (Recommended): Use physical Android device")
        print("    → Change API_SERVICE baseUrl to: http://<your-pc-ip>:8000")
        print(f"    → Example: http://{pc_ip}:8000")
        print("    → Make sure phone is on same WiFi as PC")
        print("\n  Option 2: Switch to using localhost on emulator")
        print("    → Some development tools allow this configuration")
        print("\n  Option 3: Use a different emulator or real device")
    else:
        print("\n✓ Backend appears to be correctly configured for emulator!")
        print("  Should work at: http://10.0.2.2:8000")
        print("\n  If still not connecting:")
        print("  1. Restart Flask backend")
        print("  2. Restart Android emulator")
        print("  3. Clear Flutter app cache and rebuild")
    
    if firewall_ok is False:
        print("\n⚠ Add port 8000 to Windows Firewall:")
        print("  PowerShell (as Admin):")
        print('  New-NetFirewallRule -DisplayName "FastAPI 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow')

if __name__ == "__main__":
    main()
