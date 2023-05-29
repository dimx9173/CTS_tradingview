#!/usr/bin/env python
# -*- coding: utf-8 -*-
import configparser
import ccxt
import logging
from flask import Flask
from flask import request, abort
import json
import urllib.request
import os


if os.path.exists('./bybit_config.ini'):
    config = configparser.ConfigParser()
    config.read("./bybit_config.ini", encoding="UTF-8")
else:
    logging.info("config.ini not found, program will exit")
    exit()
tradingAgents = []
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y/%m/%d/ %H:%M:%S %p"
logging.basicConfig(filename='bybit_trade.log', level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
logging.getLogger().addHandler(logging.StreamHandler())


class TradingAgent(object):
    def __init__(self, config, accountConfig):
        self.accountConfig = accountConfig
        if accountConfig is not None:
            self.exchange = ccxt.bybit(config={
                'apiKey': accountConfig.get('apiKey'),
                'secret': accountConfig.get('secret'),
                'verbose': False,  # for debug output
                'options': {
                    'defaultType': 'future',
                },
            })
        else:
            raise Exception("accountConfig not found, program will exit")
        self.config = config
        self.apiSec = config.get('service', 'api_sec')
        self.listenHost = config.get('service', 'listen_host')
        self.listenPort = config.get('service', 'listen_port')
        self.debugMode = config.get('service', 'debug_mode')
        self.ipWhiteList = config.get('service', 'ip_white_list').split(",")
        self.lastOrdId = 0 #last order id
        self.lastOrdType = None #limit/market/market-limit
        self.lastOrdSide = None #buy/sell
        self.lastOrdPosition = None #long/short/flat

    # close all position
    def closeAllPosition(self, _symbol):
        try:
            # get a list of all open positions
            positions = self.exchange.fetch_positions(symbols=[_symbol])
            logging.info("positions: " + str(positions))
            res = "closeAllPosition res: done"
            if positions[0]['contracts'] > 0.0 :
                if positions[0]['side'] == "long":
                    _side = "sell"
                else :
                    _side = "buy"
                res = self.createOrder(_symbol=_symbol, _amount=positions[0]['contracts'], _side=_side)
            
            logging.info("closeAllPosition res: " + json.dumps(res))

            return True
        except Exception as e:
            logging.error("closeAllPosition err: " + str(e))
            return False

    # create order
    def createOrder(self, _symbol, _amount, _side, _price=None, _ordType=None):
        try:
            logging.info("createOrder:symbol:{symbol},side:{side},amount:{amount},price:{price},ordType:{ordType}"
                         .format(symbol=_symbol, side=_side, amount=_amount
                             ,price=_price, ordType=_ordType))
            res = None
            if _ordType == 'limit': #limit
                #check price is valid
                if _price is None:
                    return False, "price is not valid"
                res = self.exchange.create_limit_order(symbol=_symbol, side=_side, amount=_amount,price=_price)
            elif _ordType == 'market' : #market
                res = self.exchange.create_market_order(symbol=_symbol, side=_side, amount=_amount)
            elif _ordType == 'market-limit' : #market-limit
                return False, "market-limit not support yet"
            
            logging.info("createOrder res:{res}".format(res=res))
            global lastOrdId,config
            if res:
                lastOrdId = res['id']
                return True, "create order successfully,lastOrdId:{lastOrdId}".format(lastOrdId=lastOrdId)
            return False, "create order failed"
        except Exception as e:
            logging.error("createOrder " + str(e))
            return False, str(e)
    
    # cancel last order
    def cancelLastOrder(self, _symbol):
        try:
            res = self.exchange.cancel_all_orders(symbol=_symbol,params={})
            logging.info("cancelLastOrder res: " + json.dumps(res))
            return True
        except Exception as e:
            logging.error("cancelLastOrder err: " + str(e))
            return False

    # Get instruments
    def initInstruments(self):
        c = 0
        #logging.info(exchange.load_markets(params={"symbol":"BTCUSDT"}))
        try:
            # 获取永续合约基础信息
            swapInstrumentsRes = self.exchange.fetch_markets(params={"type":"spot"})
            #logging.info("swapInstrumentsRes:{res}".format(res=swapInstrumentsRes))
            if len(swapInstrumentsRes) > 0:
                global swapInstruments
                swapInstruments = swapInstrumentsRes
                c = c + 1
        except Exception as e:
            logging.error("fetch_markets SWAP " + str(e))
        try:
            # 获取交割合约基础信息
            futureInstrumentsRes = self.exchange.fetch_markets(params={"type": "future"})
            #logging.info("futureInstrumentsRes:{res}".format(res=futureInstrumentsRes))
            if len(futureInstrumentsRes) > 0:
                global futureInstruments
                futureInstruments = futureInstrumentsRes
                c = c + 1
        except Exception as e:
            logging.error("fetch_markets FUTURES " + str(e))
        return c >= 2

    def order(self, request):
        ret = {
            "cancelLastOrder": False,
            "closedPosition": False,
            "createOrderRes": False,
            "msg": ""
        }
        #logging.info("fetch_orders={orderlist}".format(orderlist=exchange.fetch_orders(symbol="ETH-PERP", limit=200)))
        # Get parameters or fill default parameters
        _params = request.json
        if "apiSec" not in _params or _params["apiSec"] != self.apiSec:
            ret['msg'] = "Permission Denied."
            return ret
        if "symbol" not in _params:
            ret['msg'] = "Please specify symbol parameter."
            return ret
        if "amount" not in _params:
            ret['msg'] = "Please specify amount parameter."
            return ret
        if "side" not in _params:
            ret['msg'] = "Please specify side parameter"
            return ret

        #Note: When opening an order, the original position will be closed first, and then your long order will be placed
        #self.lastOrdType = None #limit/market/market-limit
        #self.lastOrdSide = None #buy/sell
        #self.lastOrdPosition = None #long/short/flat

        # cancel last order
        ret["cancelLastOrder"] = self.cancelLastOrder(_params['symbol'])
        if config.get('trading', 'single_reset') == 'true':
            # close all position if last position is different from current position
            if self.lastOrdSide != _params['side'] or self.lastOrdPosition != _params['position']:
                ret["closedPosition"] = self.closeAllPosition(_params['symbol'])

        # close all position if position is flat
        if _params['position'] == 'flat':
            ret["closedPosition"] = self.closeAllPosition(_params['symbol'])
            self.lastOrdType = _params['ordType']
            self.lastOrdSide = _params['side']
            self.lastOrdPosition = _params['position']
        elif _params['amount'] < 0.001:
            ret['msg'] = 'Amount is too small. Please increase amount.'
        else:
            # create new order
            ret["createOrderRes"], ret['msg'] = self.createOrder(_symbol=_params['symbol'], _amount=_params['amount'],
                                                            _price=_params['price'], _side=_params['side'],
                                                _ordType=_params['ordType'])
            self.lastOrdType = _params['ordType']
            self.lastOrdSide = _params['side']
            self.lastOrdPosition = _params['position']
        return ret


app = Flask(__name__)

@app.before_request
def before_req():
    if request.json is None:
        abort(400)
    if request.remote_addr not in config.get("service", "ip_white_list").split(","):
        abort(403)
    if "apiSec" not in request.json or request.json["apiSec"] != config.get("service", "api_sec"):
        abort(401)



@app.route('/order/bybit/sub<int:sub_num>', methods=['POST'])
def order_handler(sub_num: int) -> dict:
    sub_num = sub_num - 1
    ret = {}  # or any other processing specific to the routes
    if tradingAgents[sub_num] is None:
        ret['msg'] = "Unknown trading agent"
    logging.info("order_handler sub_num:{sub_num}".format(sub_num=sub_num))
    logging.info("tradingAgents[sub_num]:{sub_num}, accountConfig:{accountConfig}"
                 .format(sub_num=sub_num, accountConfig=tradingAgents[sub_num].accountConfig))
    ret = tradingAgents[sub_num].order(request)
    return ret

if __name__ == '__main__':
    try:
        ip = json.load(urllib.request.urlopen('http://httpbin.org/ip'))['origin']
        logging.info("[bybit] trading agent started\n")
        logging.info(
            "Listening on http://{listenHost}:{listenPort}".format(
                listenPort=config.get("service", "listen_port"), listenHost=config.get("service", "listen_host")))
        logging.info(
            "Access URL:http://{ip}:{listenPort}/order/bybit/subX".format(
                listenPort=config.get("service", "listen_port"), ip=ip))
        logging.info("Don't close this window, if you want to use this service")


        # Loop through the sections in the config file
        for section in config.sections():
            # Check if the section name starts with "account.sub."
            if section.startswith("account.sub."):
                # Retrieve the user and name from the section
                name = config.get(section, 'name')
                apiKey = config.get(section, 'api_key')
                secret = config.get(section, 'secret')
                # Create a dictionary to store the account details
                accountConfig = {
                    'name': name
                    , 'apiKey': apiKey
                    , 'secret': secret
                }
                tradingAgent = TradingAgent(config=config, accountConfig=accountConfig)
                #Initialize Instruments
                if tradingAgent.initInstruments() is False:
                    msg = "Initialize Instruments failed"
                    raise Exception(msg)
                tradingAgents.append(tradingAgent)

        if len(tradingAgents) <= 0:
            raise Exception("No trading agents")

        # service started
        app.run(debug=config.getboolean('service','debug_mode')
                , port=config.getint('service','listen_port')
                , host=config.get('service','listen_host'))
    except Exception as e:
        logging.error(e)
        pass
