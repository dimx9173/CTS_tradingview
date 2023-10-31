#!/usr/bin/env python
# -*- coding: utf-8 -*-
import configparser
from re import M, T
import ccxt
import logging
from flask import Flask
from flask import request, abort
import json
import urllib.request
import os
import requests
import core.MessageSender as MessageSender

if os.path.exists('./bybit_config.ini'):
    config = configparser.ConfigParser()
    config.read("./bybit_config.ini", encoding="UTF-8")
else:
    logging.info("config.ini not found, program will exit")
    exit()
tradingAgents = []

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
        logging.info("[closeAllPosition] symbol:{symbol}".format(symbol=_symbol))
        try:
            # get a list of all open positions
            positions = self.exchange.fetch_positions(symbols=[_symbol])
            logging.info("[closeAllPosition] positions: " + str(positions))
            res = "res: done"
            if positions[0]['contracts'] > 0.0 :
                if positions[0]['side'] == "long":
                    _side = "sell"
                else :
                    _side = "buy"
                res = self.createOrder(_symbol=_symbol, _amount=positions[0]['contracts'], _side=_side)
            
            logging.info("[closeAllPosition] res: " + json.dumps(res))

            return True
        except Exception as e:
            logging.error("[closeAllPosition] err: " + str(e))
            return False

    # create order
    def createOrder(self, _symbol, _amount, _side, _price=None, _ordType='market'):
        try:
            logging.info("[createOrder] symbol:{symbol},side:{side},amount:{amount},price:{price},ordType:{ordType}"
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
            
            logging.info("[createOrder] res:{res}".format(res=res))
            global lastOrdId,config
            if res:
                lastOrdId = res['id']
                return True, "create order successfully,lastOrdId:{lastOrdId}".format(lastOrdId=lastOrdId)
            return False, "create order failed"
        except Exception as e:
            logging.error("[createOrder] err:" + str(e))
            return False, str(e)
    
    # cancel last order
    def cancelLastOrder(self, _symbol):
        logging.info("[cancelLastOrder] symbol:{symbol}".format(symbol=_symbol))
        try:
            res = self.exchange.cancel_all_orders(symbol=_symbol,params={})
            logging.info("[cancelLastOrder] res: " + json.dumps(res))
            return True
        except Exception as e:
            logging.error("[cancelLastOrder] err: " + str(e))
            return False

    # Get instruments
    def initInstruments(self):
        c = 0
        logging.info("[initInstruments] accountName:{accountName}".format(accountName=self.accountConfig.get('name')))
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

    def runOrder(self, ret, _params):
        logging.info("[runOrder] ret:{ret} , _params:{_params}".format(ret=ret, _params=_params))
        #Note: When opening an order, the original position will be closed first, and then your long order will be placed
        #self.lastOrdType = None #limit/market/market-limit
        #self.lastOrdSide = None #buy/sell
        #self.lastOrdPosition = None #long/short/flat
        try:
            # cancel last order
            ret["cancelLastOrder"] = self.cancelLastOrder(_params['symbol'])
            logging.info("[runOrder] cancelLastOrder res:{res}".format(res=ret))

            if config.getboolean('trading', 'single_reset'):
                logging.info("[single_reset]")
                # close all position if last position is different from current position
                if self.lastOrdSide != _params['side'] or self.lastOrdPosition != _params['position']:
                    ret["closedPosition"] = self.closeAllPosition(_params['symbol'])
                    logging.info("[runOrder] single_reset closedPosition res:{res}".format(res=ret))

            # close all position if position is flat
            if _params['position'] == 'flat' and ret["closedPosition"] is None:
                ret["closedPosition"] = self.closeAllPosition(_params['symbol'])
                self.lastOrdType = _params['ordType']
                self.lastOrdSide = _params['side']
                self.lastOrdPosition = _params['position']
                logging.info("[runOrder] closedPosition res:{res}".format(res=ret))
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
                logging.info("[runOrder] createOrderRes res:{res}".format(res=ret))
        except Exception as e:
            logging.error("[runOrder] err: {err}".format(err=e))
        return ret

    def orderCommon(self, request):
        logging.info("[order] accountName:{accountName}".format(accountName=self.accountConfig.get('name')))
        ret = {
            "accountName":self.accountConfig.get('name'),
            "cancelLastOrder":None,
            "closedPosition": None,
            "createOrderRes": None,
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


        return self.runOrder(ret, _params)
    
    def orderLeftTurn(self, request):
        '''
        左側拐點｜多方平倉｜45m｜$1606.11
        左側拐點｜多方進場｜45m｜$1587.74
        左側拐點｜多方停損｜45m｜$1517.74
        '''
        payload = request.get_data().decode('utf-8').split('｜')

        if len(payload) != 0 and '左側拐點' not in payload:
            logging.info("[orderLeftTurn] not a left turn case ,payload:{payload}".format(payload=payload))
            return
        logging.info("[orderLeftTurn] accountName:{accountName}, payload:{payload}".
                     format(accountName=self.accountConfig.get('name'), payload=payload))

        ret = {
            "case" : "leftTurn",
            "accountName":self.accountConfig.get('name'),
            "cancelLastOrder":None,
            "closedPosition": None,
            "createOrderRes": None,
            "msg": ""
        }
        _params = {}
        _params["ordType"] = "market"
        _params["symbol"] = self.accountConfig.get('default_symbol')
        _params["amount"] = float(self.accountConfig.get('default_amount'))
        _params["price"] = float(payload[3].split('$')[1])
        _params["side"] = "buy"

        if "多方進場" in payload:    
            _params["position"] = "long"
        elif "多方停損" in payload:
            _params["position"] = "flat"
        elif "多方平倉" in payload:
            _params["position"] = "flat"
        #logging.info("[orderLeftTurn] _params:{_params}".format(_params=_params))

        return self.runOrder(ret, _params)


        

def sendMessage(msg):
    messageSender = None
    try:
        messageSender = MessageSender.MessageSender(configPath='./core/MessageSender.cfg')
        messageSender.sendMessageToMq(msg)
    except Exception as e:
        logging.error("sendMessage err:" + str(e))
    finally:
        if messageSender is not None:
           messageSender.Stop()


app = Flask(__name__)



@app.before_request
def before_req():
    logging.info("request_header:{request_header}".format(request_header = request.headers))
    payload = None
    payload_json = None

    if request.remote_addr not in config.get("service", "ip_white_list").split(","):
        abort(403)
    
    try:
        payload_json = request.get_json()
    except Exception as e:
        logging.info("not a json request")
        payload = request.get_data().decode('utf-8')

    logging.info("request_data:{request_data}".format(request_data = payload))

    if payload_json is not None:
        logging.info("payload is json")
        if payload_json["apiSec"] != config.get("service", "api_sec"):
            logging.warn("no apiSec in request")
            abort(401)
    elif payload is not None and len(payload) != 0:
        logging.info("payload is text/plain")
        specific_keys = config.get("service", "specific_keys").split(",")
        for key in specific_keys:
            if key in payload:
                logging.info("get specific key:{key} in payload".format(key=key))
                return
        logging.warn("not specific key:{specific_keys} in payload".format(specific_keys=specific_keys))
        abort(404)
    else:
        logging.warn("not a valid request")
        abort(400)



@app.route('/order/bybit/sub<int:url_num>', methods=['POST'])
def order_handler(url_num: int) -> dict:
    logging.info("order_handler url_num:{url_num}".format(url_num=url_num))
    sub_num = url_num - 1
    ret = {}# or any other processing specific to the routes
    agent = tradingAgents[sub_num]
    if agent is None:
        ret['msg'] = "Unknown trading agent "
    msg = "tradingAgents[{sub_num}]:{url_num}, accountName:{accountName}, request:{request}"
    msg = msg.format(sub_num=sub_num,url_num=url_num,
                     accountName=agent.accountConfig.get('name'),
                     request=request.get_data().decode('utf-8'))
    logging.info(msg)
    sendMessage(msg)
    if '左側拐點' in request.get_data().decode('utf-8'):
        ret = agent.orderLeftTurn(request)
    else:
        ret = agent.orderCommon(request)
    return ret

if __name__ == '__main__':
    LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
    DATE_FORMAT = "%Y/%m/%d/ %H:%M:%S %p"
    logging.basicConfig(filename='bybit_trade.log', level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    logging.getLogger().addHandler(logging.StreamHandler())
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
                    , 'default_symbol': config.get(section, 'default_symbol')
                    , 'default_amount': config.get(section, 'default_amount')
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
    finally:
        pass



