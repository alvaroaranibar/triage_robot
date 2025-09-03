import time
import VL53L0X
import max30102, hrcalc
import board
import busio as io
import adafruit_mlx90614
import numpy as np
import datetime as dt
import pytz
import urllib.request
from Adafruit_IO import Client, ThrottlingError
import asyncio
from bleak import BleakClient
import struct
from collections import deque
import boto3 #Lambda
import json #Lambda

timeZone = 'America/Lima'

# CONFIGURACION E INICIALIZACION DE SENSORES
try:
    m = max30102.MAX30102() # sensor initialization
except:
    print("ERROR EN CONFIG. DE MAX30102")

try:
    i2c = io.I2C(board.SCL, board.SDA, frequency=100000)
    mlx = adafruit_mlx90614.MLX90614(i2c)
except:
    print("ERROR EN CONFIG. DE MLX90614")

try:
    tof = VL53L0X.VL53L0X()
except:
    print("ERROR EN CONFIG. DE VL53L0X")
    
###Adafruit IO setup
io = Client('robot_triaje', 'aio_YIyb72bZbNL1cgcgpfGsnLz50iEa')
try: # Check if we have internet connectivity
    urllib.request.urlopen('http://www.google.com').close()
    internet = True
except:
    internet = False

if internet:
    f0 = io.feeds('historias')
else:
    print("No internet")


# LAMBDA: CONFIGURA CLIENTE
lambda_client = boto3.client('lambda', region_name='us-east-2')


# FUNCIONES DE SENSORES

def mide_altura(h_mesa, n_med_altura): # h_mesa (en mm)
    #hmesa 906
    h_sensor = 1196   #MODIFICAR: ALTURA DEL SENSOR CON RESPECTO DE LA BASE DEL ROBOT (MESA)
    h_balanza = 22   #altura de la balanza ya que la medicion se hace cuando la persona se para en la balanza
    try:
        distancias = []
        tof.start_ranging(VL53L0X.VL53L0X_BETTER_ACCURACY_MODE)
        timing = tof.get_timing()
        if timing < 20000:
            timing = 20000
            
        suma = 0
        count_reg = 0

        for count_med in range(1, n_med_altura):
            distance = tof.get_distance()
            if distance > 0:
                distancias.append(distance)
                print("%d mm, %d cm, %d" % (distance, (distance/10), count_reg))
            time.sleep(timing/1000000.00)

        tof.stop_ranging()
        # Falta hacer los calculos para medir altura
        print(min(distancias))
        h_persona = h_mesa + h_sensor - h_balanza - min(distancias)
        h_persona = h_persona/1000 #Se convierte a M
        print("Altura persona (m) : ",h_persona)
    except:
        print("ERROR EN MEDICION DE ALTURA")
    else:
        return h_persona  # Retorna el promedio de las medidas registradas en caso de que no haya ningun error
    
def mide_temp():
    arr_temp = []
    while True:
        Ta = mlx.ambient_temperature + 273.15 #Se convierte a Kelvin
        Tom = mlx.object_temperature + 273.15
        time.sleep(0.15)
        eps = 0.98 #constante de emisividad de la piel(Por defecto el chip usa eps =1)
        #Correccion de temperatura por emisividad
        Toc = pow(((pow(Tom,4)-pow(Ta,4))/eps) + pow(Ta,4), 0.25)
        Toc = Toc - 273.15 #Se regresa a Celsius
        Ta=Ta-273
        print("Ambient Temperature:", Ta, "C")
        print("Target Temperature:", Toc, "C")
        
        arr_temp.append(Toc)
        #Mantener solo las últimas tres mediciones
        if len(arr_temp)>5:
            arr_temp.pop(0)
        if len(arr_temp) == 5:
            diferencia_maxima = max(arr_temp) - min(arr_temp)
            # Solo registra valor cuando diferencia entre las tres ultimas mediciones es menor a 0.8 (estable) y mayor a 30.5 °C
            if diferencia_maxima < 0.8 and min(arr_temp)>30.5 : 
                print(f"Toc final: {Toc}, Ta final: {Ta}")
                return Toc, Ta

def mide_pulso():
    buffer = deque(maxlen=3)  # Buffer circular de 3 elementos
    ultima_sp = None  # Para almacenar la última medida2 registrada

    while True:
        red, ir = m.read_sequential()  # Toma 100 medidas de los LEDs
        hr, hrb, sp, spb = hrcalc.calc_hr_and_spo2(ir, red)  # Calcula parametros y si son validos

        if( hrb == True and hr != -999 and spb == True and sp != -999):
            print("Heart Rate : ",hr)
            print("SPO2       : ",sp)
            buffer.append(hr)
            # Verifica si medida2 tiene dos valores iguales o difieren en 1 unidad
            if ultima_sp is not None and abs(sp - ultima_sp) <= 1:
                if len(buffer) == 3:  # Asegura que el buffer esté lleno para calcular el promedio
                    hr_prom = sum(buffer) / len(buffer)
                    print(f"sp final: {sp}, hr final: {hr_prom}")
                    return hr_prom, sp
            
            ultima_sp = sp

def registra_tiempo(): # Retorna un string con la fecha y hora
    tiempo = dt.datetime.now(tz=pytz.timezone(timeZone))
    if tiempo != 'None':
        tiempo_str = tiempo.strftime('%d.%m.%y %H:%M')
    else:
        tiempo_str = tiempo
    return tiempo_str

# scale: a string with the MAC address of the scale
scale = "5C:64:F3:5B:0B:9E"
# uuid: a string with the UUID of the characteristic to read
uuid = "00002a9d-0000-1000-8000-00805f9b34fb"

async def noti(sender, data):
    global peso1
    try:
        peso_data1 = struct.unpack("H", data[1:3])[0] / 200.0
        print(f"Midiendo peso: {peso_data1}kg")
        peso1 = peso_data1
    except struct.error as e:
        print(f"Error: {e}")

async def start_connection():
    try:
        async with BleakClient(scale) as client:
            await client.start_notify(uuid, noti)
            await asyncio.sleep(10)
            await client.stop_notify(uuid)
    except Exception as e:
        print(f"No se pudo conectar con el dispositivo: {e}")

def obtener_peso():
    global peso1
    peso1 = None  # Reset the peso variable
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_connection())
    print("Peso final: ", peso1)
    return peso1

# CONVIERTE DATOS A JSON
def procesar_datos(tiempo, nombre, dni, edad, altura, peso, hr, sp, temp_user, sintomas):
    tiempo_str = str(tiempo)
    nombre_str = str(nombre)
    dni_str = str(dni)
    edad_str = str(edad)
    altura_str = str(round(altura,2))
    peso_str = str(round(peso,2))
    hr_str = str(round(hr,2))
    sp_str = str(round(sp,2))
    temp_user_str = str(round(temp_user,2))
    sintomas_str = str(sintomas)

    # Construir el JSON con los campos correspondientes
    datos = {
        "DNI": dni_str,                     # DNI
        "tiempo": tiempo_str,                    # Hora
        "nombre_completo": nombre_str,         # Nombre completo
        "edad": edad_str,               # Edad
        "altura": altura_str,             # Altura
        "peso": peso_str,               # Peso
        "temperatura": temp_user_str,      # Temperatura
        "frecuencia_cardiaca": hr_str, # Frecuencia Cardíaca
        "saturacion_oxigeno": sp_str,  # Saturación de Oxígeno
        "sintomas": sintomas_str                # Síntomas
    }
    return datos

# Función para enviar los datos a Lambda
def probar_lambda(json_data, lambda_function_name):
    if json_data == {}:  # Caso de datos vacíos
        print("Enviando datos vacíos para borrar registros en DynamoDB...")

    if not json_data:
        print("No hay datos para enviar a Lambda.")
        return

    # Imprimir el JSON antes de enviarlo
    print("Datos JSON enviados a Lambda:", json.dumps(json_data, indent=4))

    try:
        response = lambda_client.invoke(
            FunctionName=lambda_function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(json_data)
        )

        
        respuesta = response['Payload'].read().decode('utf-8')
        print("Respuesta de Lambda:", respuesta)

    except boto3.exceptions.Boto3Error as e:
        print("Error al invocar Lambda con boto3:", e)

    except Exception as e:
        print("Error general al invocar Lambda:", e)

h_mesa  = input("Ingrese la altura de la mesa (en mm) para calibrar sensor:")
h_mesa = int(h_mesa)
print("Altura mesa:",h_mesa)

while True:
    nom = ""
    ape = ""
    nombre = ""
    tiempo = ""
    altura = 0
    peso = 0
    hr = 0
    sp = 0
    temp_user = 0
    temp_amb = 0

    nom = input("Ingrese su nombre: ").strip()
    ape = input("Ingrese su apellido: ").strip()
    nombre= nom + "" + ape
    tiempo = registra_tiempo()
    dni = input("Ingrese su DNI: ").strip()
    edad = input("Ingrese su edad: ").strip()
    sintomas = input("Ingrese sus sintomas (maximo 5, separados por espacios): ").strip()
    
    respuesta = input("Deseas medir el peso? (s/n): ").strip().lower()
    if respuesta == "s":
        peso = obtener_peso()

    respuesta = input("Desea medir altura? (s/n): ").strip().lower()
    if respuesta == "s":
        altura = mide_altura(h_mesa, 130)  #Numero de mediciones: 130
                                          #Si se desea menor tiempo de medicion, disminuir numero de mediciones

    respuesta = input("Desea medir pulsioximetria? (s/n): ").strip().lower()
    if respuesta == "s":
        hr, sp = mide_pulso() 

    respuesta = input("Desea medir temperatura? (s/n): ").strip().lower()
    if respuesta == "s":
        temp_user, temp_amb = mide_temp()
        
    # Maneja el error segun sea necesario
    # Crea string separado por comas
    line = '%s,%s,%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%s\n' % (tiempo, nombre, dni, edad, altura, peso, hr, sp, temp_user, temp_amb, sintomas)
    print('%s' % line[:-1])

    #GENERAR JSON Y ENVIAR DATOS LAMBDA
    datos_json = procesar_datos(tiempo, nombre, dni, edad, altura, peso, hr, sp, temp_user, sintomas)
    if datos_json:
        probar_lambda(datos_json, "Datos_para_dynamo")

    # GUARDA EN CSV EEN CARPETA DATA
    # Agenda string en csv, 1 nuevo archivo por cada dia, si no existe lo crea
    with open('./DATA/' + str(dt.date.today()) + '.csv', 'a') as log:
        log.write(line)

    # ENVIA A SERVIDOR
    try:
        urllib.request.urlopen('http://www.google.com').close()
        internet = True
    except urllib.error.URLError:
        internet = False
        print('\nNo internet connection')
    if internet:
        try:
            a = f0.key
        except:
            f0 = io.feeds('all')
        try:
            io.send(f0.key, line[:-1])
        except ThrottlingError:
            # Just in case for some reason we exceed Adafruit's maximum rate, wait a bit
            print('ThrottlingError')
            time.sleep(30)
