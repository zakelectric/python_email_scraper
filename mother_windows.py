import subprocess
import time
import os
import psutil

def kill_all_scraper_processes():
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Check if 'scraper_automated.py' is in the command line
            if proc.info['cmdline'] and 'scraper_automated.py' in ' '.join(proc.info['cmdline']):
                print(f"Killing process {proc.pid} ({proc.info['name']})")
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

try:
    proc = subprocess.Popen(['cmd.exe', '/c', 'start', '', 'python', 'scraper_automated.py'], shell=True)
except FileNotFoundError:
    proc = subprocess.Popen(['python', 'scraper_automated.py'])

while True:
    time_now = time.localtime()
    print("Time:", time.strftime("%Y-%m-%d %H:%M:%S", time_now))

    if os.path.exists('running.flag'):
        os.remove('running.flag')
    
    time.sleep(300)
    
    if os.path.exists('running.flag'):
        continue
    else:
        kill_all_scraper_processes()

        try:
            proc = subprocess.Popen(['cmd.exe', '/c', 'start', '', 'python', 'scraper_automated.py'], shell=True)
        except FileNotFoundError:
            proc = subprocess.Popen(['python', 'scraper_automated.py'])