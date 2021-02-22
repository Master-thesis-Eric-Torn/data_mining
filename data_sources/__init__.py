from nordpool import elspot, elbas
import requests
import json
import tibber
import numpy as np
import pickle
import sys
import data_mining.data_sources.sensibo_client as SC
import asyncio
from datetime import date, datetime, timedelta, timezone
import pytz
import sched
import time
import asyncio
from pathlib import Path
import os
from uuid import uuid1
import logging

LOCAL_TIMEZONE = pytz.timezone('Europe/Oslo')


class Sensor():
    '''
    Parent class which is inherited by all sensor classes.
    '''
    def __init__(self):
        logging.basicConfig(filename="datamining_errors.log",
                            format='%(asctime)s %(message)s', level=logging.DEBUG)
        self._session_id = str(uuid1())[:8]
        self._initialize_data_folders()

    async def start_mining(self, time_ending=None):
        scheduled_time = self._get_first_scheduled_time()
        self.time_mining_started = scheduled_time
        self.time_ending = time_ending

        while self.time_ending is None or scheduled_time < self.time_ending:
            time_now = datetime.now(tz=LOCAL_TIMEZONE)
            time_until_measurement = (
                scheduled_time - time_now).total_seconds()
            if time_until_measurement > 0:
                await asyncio.sleep(time_until_measurement)

            # _get_latest_measurement() is unique for each sensor and is defined in each sensor file
            await self._get_latest_measurement()
            self._save_data_to_file()

            # set the time for next measurement
            scheduled_time += timedelta(seconds=self.sampling_time)

    def _save_data_to_file(self):
        time_now = datetime.now(tz=LOCAL_TIMEZONE)
        
        folder_daily = self.sensor_name + '/daily/'
        filename_daily = self.sensor_name + '_' + \
            time_now.strftime('%Y-%m-%d') + '_' + self._session_id

        folder_weekly = self.sensor_name + '/weekly/'
        filename_weekly = self.sensor_name + '_' + \
            time_now.strftime('%Y-week-%V') + '_' + self._session_id

        folder_monthly = self.sensor_name + '/monthly/'
        filename_monthly = self.sensor_name + '_' + \
            time_now.strftime('%Y-month-%m') + '_' + self._session_id

        folder_yearly = self.sensor_name + '/yearly/'
        filename_yearly = self.sensor_name + '_' + \
            time_now.strftime('%Y') + '_' + self._session_id

        try:
            with open(folder_daily+filename_daily, "wb") as f:
                pickle.dump(self.data, f)
            return
            with open(folder_weekly+filename_weekly, "wb") as f:
                pickle.dump(self.data, f)
            return
            with open(folder_monthly+filename_monthly, "wb") as f:
                pickle.dump(self.data, f)
            return
            with open(folder_yearly+filename_yearly, "wb") as f:
                pickle.dump(self.data, f)
            return
        except Exception as e:
            logging.error(
                "save_data_to_file: Something went wrong while writing data to files.")
            logging.error(str(e))
            return False
        return True

    def _get_first_scheduled_time(self):
        time_now = datetime.now(tz=LOCAL_TIMEZONE)
        dt = self.sampling_time
        if int(dt*np.ceil(time_now.minute/dt)) < 60:
            scheduled_time = time_now.replace(
                second=0, microsecond=0, minute=int(dt*np.ceil(time_now.minute/dt)))
        else:
            scheduled_time = time_now.replace(
                second=0, microsecond=0, minute=0, hour=time_now.hour+1)
        return scheduled_time

    def _initialize_data_folders(self):
        try:
            os.makedirs('data/'+self.sensor_name+'/daily')
            os.makedirs('data/'+self.sensor_name+'/weekly')
            os.makedirs('data/'+self.sensor_name+'/monthly')
            os.makedirs('data/'+self.sensor_name+'/yearly')
        except Exception as e:
            logging.error("ERROR: " + str(e))
            print("ERROR (_initialize_data_folders): " + str(e))
            return False
        return True


class TibberRealtimeSensor(Sensor):
    # TODO
    pass


class TibberAPI(Sensor):
    '''
    Tibber "sensor"/api
    =============
    Gets: 
        * consumption (kwh) for the latest hour (or for n datapoints backwards from now).
        * cost (kwh*spot_price) for the latest hour (or for n datapoints backwards from now).
        * total cost

    Data point settings:
        - periodic: "HOURLY" / "DAILY" / "WEEKLY" / "YEARLY"
            = data_points interval
        - n: > 0
            = how many datapoints to pull
    '''
    def __init__(self, api_key, sampling_time, end_mining_at):
        self.sensor_name = 'tibber'
        self.api_key = api_key
        self.sampling_time = sampling_time
        self.end_mining_at = end_mining_at
        super().__init__()

        try:
            self._tibber_conn = tibber.Tibber(self.api_key)
            self._tibber_conn.sync_update_info()
            self.homes = self._tibber_conn.get_homes()
        except Exception as e:
            print(str(e))
            print("Tibber: could not connect.")
        
        
        self._initialize_data_structure()

    async def get_latest_measurement(self):
        for home in self.homes:
            try:
                await home.sync_update_info()
            except:
                # TODO: Logger
                print("Tibber: could not sync home info")
                return False
            
            # Settings
            home_name = home.info['viewer']['home']['appNickname']
            resolution = "HOURLY"
            n = 1

            # Initialize data structure for each home
            self.data['data'][home_name] = {
                'time': [],
                'consumption': [],
                'cost': [],
                'total_cost': []
            }

            # Get data
            try:
                historic_data = await home.sync_get_historic_data(n, resolution)
                for data_point in historic_data:
                    timestamp = datetime.strptime(data_point['from'], '%Y-%m-%dT%H:%M:%S%z')
                    self.data[home_name]['time'].append(timestamp)
                    self.data[home_name]['consumption'].append(data_point['consumption'])
                    self.data[home_name]['cost'].append(data_point['cost'])
                    self.data[home_name]['total_cost'].append(data_point['totalCost'])
            except:
                # TODO: Logger
                print("Tibber: could not get historic data.")
                return False
        return True

    def _initialize_data_structure(self):
        self.data = {
            'sampling_time': self.sampling_time,
            'data': {}
        }


class SensiboSensor(Sensor):
    '''
    Sensibo sensor.
    ===============
    Measures temperature and humidity.

    The sensobi api updates with new values every 90-91 seconds,
    so to avoid duplicates, set the sampling time above this.
    '''
    def __init__(self, api_key, sampling_time, end_mining_at):
        self.sensor_name = 'sensibo'
        self.api_key = api_key
        self.sampling_time = sampling_time
        self.end_mining_at = end_mining_at
        self.what_to_measure = ['temperature', 'humidity']
        self.states_to_record = ['on', 'targetTemperature', 'fanLevel', 'mode']
        self.time_since_last_measurement = None
        super().__init__()

        try:
            self.home = SC.SensiboClientAPI(self.api_key)
            self.devices = self.home.devices()
        except:
            print('Home Sensibo could not be accessed. Code terminated.')
            sys.exit()

        
        self._initialize_data_structure()

    async def _get_latest_measurement(self):
        for pump in self.devices.keys():
            try:
                latest_measurement = await self.home.pod_measurement(self.devices[pump])[0]
                current_state = await self.home.pod_ac_state(self.devices[pump])
            except:
                # TODO: logger
                print("Sensibo get_latest_measurement: Could not get measurements or state")
                return False
            
            # TODO: Setting: "avoid duplicates"
            # if self.time_since_last_measurement != None and latest_measurement['time']['secondsAgo'] >= self.time_since_last_measurement:
            #     print("Sensibo data: Latest measurement was already recorded. Aborting until next interval to avoid duplicates.")
            #     return False
            
            for measurement_type in self.data['data'][pump]['measurements'].keys():
                if measurement_type in latest_measurement:
                    self.data['data'][pump]['measurements'][measurement_type].append(latest_measurement[measurement_type])
                else:
                    self.data['data'][pump]['measurements'][measurement_type].append(np.NaN)
                    # TODO: Logger
                    print(latest_measurement)
                    print('Sensibo measurement '+str(measurement_type) +' for '+str(pump)+' missing, put NaN ')

            for state in self.data['data'][pump]['states']:
                    if state in current_state:
                        self.data['data'][pump]['states'][state].append(current_state[state])
                    else:
                        self.data['data'][pump]['states'][state].append(np.NaN)
                        # TODO: Logger
                        print(current_state)
                        print('Sensibo state '+str(state)+' for ' + str(pump)+' missing, put NaN ')
            timestamp = datetime.strptime(latest_measurement['time']['time'], '%Y-%m-%dT%H:%M:%S.%fZ')
            utc_timestamp = timestamp.replace(tzinfo=pytz.utc)
            data[pump]['times'].append(utc_timestamp.astimezone(tz=LOCAL_TIMEZONE))
            self.time_since_last_measurement = latest_measurement['time']['secondsAgo']
        return True

    def _initialize_data_structure(self):
        self.data = {
            'sampling_time': self.sampling_time,
            'data': {}
        }
        for pump in self.devices.keys():
            self.data['data'][pump] = {'times': [],
                               'measurements': {},
                               'states': {}}

            for measurement_type in self.what_to_measure:
                self.data['data'][pump]['measurements'][measurement_type] = []

            for state in self.states_to_record:
                self.data['data'][pump]['states'][state] = []
                

class WeatherAPI(Sensor):
    '''
    Weather API
    ===========
    Using api call for current weather: https://openweathermap.org/current
    The api update interval is kinda random. The time between 3 updates on the api 
    were 342 secs between the first two and 576 secs between the last two.

    An alternative api call is the One Call: https://openweathermap.org/api/one-call-api

    '''
    def __init__(self, lat, lon, api_key, sampling_time, end_mining_at):
        self.sensor_name = 'weather'
        self.api_key = api_key
        self.sampling_time = sampling_time
        self.end_mining_at = end_mining_at
        self.lat = lat
        self.lon = lon
        self.url = f"api.openweathermap.org/data/2.5/weather?lat={self.lat}&lon={self.lon}&appid={self.api_key}&units=metric"
        super().__init__()
        self._initialize_data_structure()

    async def _get_latest_measurement(self):
        try:
            response = await requests.get(self.url, timeout=30)
            if response.status_code == 200:
                current_data = json.loads(response.text)
                timestamp = datetime.utcfromtimestamp(current_data['dt']).replace(tzinfo=timezone.utc)
                timestamp = timestamp.astimezone(LOCAL_TIMEZONE)
                
                self.data['data']['time'].append(timestamp)
                self.data['data']['temperature'] = current_data['main']['temp']

                self.current["time"] = timestamp
                self.current["temperature"] = current_data['main']['temp']
                self.last_updated = response.headers["Date"]
            else: 
                # TODO: logger
                print("Weather API: Could not get request from the URL. Check API key, lat, or lon.")
                print("Request status code: ", response.status_code)
                return False
        except:
            # TODO: logger
            print("Weather API: Could not get request from the URL. Check API key, lat, or lon.")
            return False
        return True

    def _initialize_data_structure(self):
        self.data = {
            'sampling_time': self.sampling_time,
            'data': {
                'time': [],
                'temperature': []
            }
        }
        self.current = {}


class SpotMarketAPI(Sensor):
    '''
    Spot market API
    ===============
    '''
    def __init__(self, zone='Tr.heim'):
        self.spot_prices = elspot.Prices()
        self.zone = zone

        self.sensor_name = 'spotmarket'

    def _initialize_data_structure(self):
        self.data = {
            'time': [],
            'prices': []
        }

    def _get_data(self):
        time_now = datetime.now(tz=LOCAL_TIMEZONE)
        time_noon = time_now.replace(second=0, microsecond=0, minute=0, hour=12)

        # Spot prices for next day are announces at noon every day
        if time_now > time_noon:
            day_list    = [0,1]
        else:
            day_list    = [0]

        for day in day_list:
            end_date = date.today() + timedelta(days=day)
            prices = self.spot_prices.hourly(areas=[self.zone], end_date=end_date)

        for i in range(24):
            timestamp = prices['areas'][self.zone]['values'][i]['start']  # UTC times
            timestamp = time.astimezone(LOCAL_TIMEZONE)                   # convert to local time
            price = prices['areas'][self.zone]['values'][i]['value']
            
            self.data['time'].append(time)
            self.data['prices'].append(price)