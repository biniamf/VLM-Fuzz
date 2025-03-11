
# How to run

set env variable "OPENAI_API_KEY" with your OPENAI API KEY
export OPENAI_API_KEY=you-key

- prepare the emulator and launch it
- run the script in ui_dumper directory and wait until it's ready
- run ./run.sh path/to/apk  if you have one emulator only. 

----

# Custom emulator/device port (multi emulator/device setting)

If you need to use custom emulator ports in multi emulator setting or custom budget, make sure to install the apk on the right emulator and run the following script specifying the right port and budget

python main.py [-h] -a APK [-p PORT] [-b BUDGET]

-a path/to/apk
-p emulator port if not default
-b budget in minutes, default is 60 mins
-h show help

<img src="net.everythingandroid.timer_test2.png" alt="transition example">

