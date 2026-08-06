SLEUTH

Config.py:

The [config.py](http://config.py) is a overall setting of the class that consists of a immutable frozen class that loads all the secrets from the dotenv using load_dotenv and then here we check in load_config() if three required keys are present or not voyage api key and database url and groq model. Then it downstreams - means all the modules requiring these keys will recieve a Config object which they cannot change just use it in this way they cannot reach the actual .env but a config object only.