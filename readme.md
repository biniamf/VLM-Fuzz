[![arXiv](https://img.shields.io/badge/arXiv-1234.56789-b31b1b.svg)]([https://arxiv.org/abs/1234.56789](https://arxiv.org/abs/2504.11675))

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

```bibtex
@misc{demissie2025vlmfuzzvisionlanguagemodel,
      title={VLM-Fuzz: Vision Language Model Assisted Recursive Depth-first Search Exploration for Effective UI Testing of Android Apps}, 
      author={Biniam Fisseha Demissie and Yan Naing Tun and Lwin Khin Shar and Mariano Ceccato},
      year={2025},
      eprint={2504.11675},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2504.11675}, 
}
```


