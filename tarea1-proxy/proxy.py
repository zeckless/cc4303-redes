# CC4303 Redes - Actividad 1: construccion de un proxy HTTP
# Mario Opazo - Seccion 1
# Profesora: Ivana Bachmann

import socket
import json
import sys

# buffer chico a proposito, para que el codigo tenga que manejar mensajes
# mas grandes que el buffer
BUFFER_RECEPCION = 50

# si la URI no dice el puerto, HTTP usa el 80
PUERTO_HTTP_DEFAULT = 80

# imagen que se muestra en la pagina de bloqueo
IMAGEN_BLOQUEO = "gato.jpg"
IMAGEN_CONTENT_TYPE = "image/jpeg"

# los headers se pasan a texto con latin-1 y no con utf-8, asi ningun byte
# se pierde al volver a convertirlos
CODIFICACION_HEADERS = "latin-1"


# funciones para parsear y armar mensajes HTTP

# toma los bytes de un mensaje HTTP y los deja en un diccionario
def parse_HTTP_message(http_message):
    # el doble salto de linea separa el HEAD del BODY
    partes = http_message.split(b"\r\n\r\n", 1)
    head_bytes = partes[0]
    body = partes[1] if len(partes) > 1 else b""

    # el HEAD lo pasamos a texto y lo cortamos por lineas
    lineas = head_bytes.decode(CODIFICACION_HEADERS).split("\r\n")
    linea_inicial = lineas[0]

    mensaje = {"headers": [], "body": body, "linea_inicial": linea_inicial}

    # si la start line empieza con HTTP/ es una respuesta, si no es una peticion
    if linea_inicial.upper().startswith("HTTP/"):
        campos = linea_inicial.split(" ", 2)
        mensaje["es_respuesta"] = True
        mensaje["version"] = campos[0] if len(campos) > 0 else ""
        mensaje["codigo_estado"] = campos[1] if len(campos) > 1 else ""
        mensaje["texto_estado"] = campos[2] if len(campos) > 2 else ""
    else:
        campos = linea_inicial.split(" ", 2)
        mensaje["es_respuesta"] = False
        mensaje["metodo"] = campos[0] if len(campos) > 0 else ""
        mensaje["uri"] = campos[1] if len(campos) > 1 else ""
        mensaje["version"] = campos[2] if len(campos) > 2 else "HTTP/1.1"

    # el resto de las lineas son headers con la forma "Nombre: valor"
    # los guardamos como lista de pares para no perder los que se repiten
    for linea in lineas[1:]:
        if ":" in linea:
            nombre, valor = linea.split(":", 1)
            mensaje["headers"].append([nombre.strip(), valor.strip()])

    return mensaje


# hace lo contrario: toma el diccionario y devuelve los bytes del mensaje
def create_HTTP_message(mensaje):
    # armamos la start line segun si es respuesta o peticion
    if mensaje.get("es_respuesta"):
        linea_inicial = "{} {} {}".format(
            mensaje["version"], mensaje["codigo_estado"], mensaje["texto_estado"]
        )
    else:
        linea_inicial = "{} {} {}".format(
            mensaje["metodo"], mensaje["uri"], mensaje["version"]
        )

    # pegamos cada header en su linea y cerramos el HEAD con un salto extra
    texto = linea_inicial + "\r\n"
    for nombre, valor in mensaje["headers"]:
        texto += "{}: {}\r\n".format(nombre, valor)
    texto += "\r\n"

    # el body se pega tal cual, en bytes, porque puede ser una imagen
    return texto.encode(CODIFICACION_HEADERS) + mensaje["body"]


# busca un header sin importar mayusculas o minusculas
def obtener_header(mensaje, nombre):
    for n, v in mensaje["headers"]:
        if n.lower() == nombre.lower():
            return v
    return None


# cambia el valor de un header, y si no existe lo agrega al final
def poner_header(mensaje, nombre, valor):
    for par in mensaje["headers"]:
        if par[0].lower() == nombre.lower():
            par[1] = valor
            return
    mensaje["headers"].append([nombre, valor])


# borra un header
def borrar_header(mensaje, nombre):
    mensaje["headers"] = [
        p for p in mensaje["headers"] if p[0].lower() != nombre.lower()
    ]


# recibir mensajes de a poco, por si el buffer es mas chico que el mensaje

# junta bytes hasta encontrar el \r\n\r\n que marca el fin del HEAD
def recibir_head(sock, tamano_buffer):
    datos = b""
    while b"\r\n\r\n" not in datos:
        trozo = sock.recv(tamano_buffer)
        # si llega vacio, cerraron la conexion antes de terminar el HEAD
        if not trozo:
            return datos, b""
        datos += trozo

    # cortamos justo despues del delimitador
    corte = datos.find(b"\r\n\r\n") + 4
    # devolvemos el HEAD y lo que ya venia del BODY, para no perderlo
    return datos[:corte], datos[corte:]


# sigue recibiendo hasta juntar la cantidad de bytes que dice Content-Length
def recibir_n_bytes(sock, tamano_buffer, ya_recibido, largo_total):
    body = ya_recibido
    while len(body) < largo_total:
        trozo = sock.recv(tamano_buffer)
        if not trozo:
            break
        body += trozo
    return body


# recibe hasta que el otro lado cierra la conexion
def recibir_hasta_cierre(sock, tamano_buffer, ya_recibido):
    body = ya_recibido
    while True:
        trozo = sock.recv(tamano_buffer)
        if not trozo:
            break
        body += trozo
    return body


# junta los trozos de un body que viene con Transfer-Encoding: chunked
# cada trozo viene como: largo en hexadecimal, \r\n, datos, \r\n
def desarmar_chunked(body):
    salida = b""
    resto = body
    while True:
        fin_linea = resto.find(b"\r\n")
        if fin_linea == -1:
            break
        # la primera linea del trozo trae su largo en hexadecimal
        cabecera = resto[:fin_linea].split(b";")[0].strip()
        try:
            largo = int(cabecera, 16)
        except ValueError:
            break
        # un trozo de largo 0 significa que ya no quedan mas
        if largo == 0:
            break
        inicio = fin_linea + 2
        salida += resto[inicio:inicio + largo]
        resto = resto[inicio + largo:]
        if resto.startswith(b"\r\n"):
            resto = resto[2:]
    return salida


# recibe un mensaje HTTP completo y lo devuelve parseado
def recibir_mensaje_HTTP(sock, tamano_buffer):
    # primero el HEAD, que es lo unico que sabemos delimitar de entrada
    head_bytes, sobrante = recibir_head(sock, tamano_buffer)
    if not head_bytes:
        return None

    mensaje = parse_HTTP_message(head_bytes)

    # ahora que tenemos los headers ya podemos saber cuanto BODY falta
    transfer_encoding = obtener_header(mensaje, "Transfer-Encoding") or ""
    content_length = obtener_header(mensaje, "Content-Length")

    if "chunked" in transfer_encoding.lower():
        # viene por trozos: los juntamos y lo dejamos como un body normal
        crudo = recibir_hasta_cierre(sock, tamano_buffer, sobrante)
        mensaje["body"] = desarmar_chunked(crudo)
        borrar_header(mensaje, "Transfer-Encoding")
        poner_header(mensaje, "Content-Length", str(len(mensaje["body"])))
    elif content_length is not None:
        # caso normal: sabemos exactamente cuantos bytes esperar
        try:
            largo = int(content_length)
        except ValueError:
            largo = 0
        mensaje["body"] = recibir_n_bytes(sock, tamano_buffer, sobrante, largo)
    elif mensaje["es_respuesta"]:
        # respuesta sin Content-Length: el fin lo marca el cierre de conexion
        mensaje["body"] = recibir_hasta_cierre(sock, tamano_buffer, sobrante)
    else:
        # una peticion sin Content-Length no trae body (un GET normal)
        # ojo: aca NO hay que leer hasta el cierre o el proxy queda colgado
        mensaje["body"] = b""

    return mensaje


# logica del proxy

# saca el host, el puerto y el path del servidor al que quiere llegar el cliente
def extraer_destino(mensaje):
    uri = mensaje.get("uri", "")

    # cuando el cliente usa un proxy manda la URI completa, con http:// adelante
    if uri.startswith("http://"):
        sin_esquema = uri[len("http://"):]
    elif uri.startswith("https://"):
        sin_esquema = uri[len("https://"):]
    else:
        sin_esquema = None

    if sin_esquema is not None:
        # separamos lo que va antes del primer / (el host) del resto (el path)
        if "/" in sin_esquema:
            autoridad, resto = sin_esquema.split("/", 1)
            path = "/" + resto
        else:
            autoridad, path = sin_esquema, "/"
    else:
        # si la URI venia relativa, sacamos el host del header Host
        autoridad = obtener_header(mensaje, "Host") or ""
        path = uri if uri.startswith("/") else "/" + uri

    # la autoridad puede venir como "host" o como "host:puerto"
    if ":" in autoridad:
        host, puerto_txt = autoridad.rsplit(":", 1)
        try:
            puerto = int(puerto_txt)
        except ValueError:
            puerto = PUERTO_HTTP_DEFAULT
    else:
        host, puerto = autoridad, PUERTO_HTTP_DEFAULT

    return host, puerto, path


# le quita el http:// y el / final a una entrada de la lista negra
def normalizar_bloqueado(entrada):
    e = entrada.strip()
    for esquema in ("http://", "https://"):
        if e.startswith(esquema):
            e = e[len(esquema):]
    return e.rstrip("/")


# revisa si el destino esta en la lista de bloqueados del json
def esta_bloqueado(host, path, blocked):
    destino = (host + path).rstrip("/")
    solo_host = host.rstrip("/")

    # las entradas del json pueden ser solo el dominio o el dominio con path,
    # asi que comparamos contra las dos formas
    for entrada in blocked:
        e = normalizar_bloqueado(entrada)
        if not e:
            continue
        if solo_host == e or destino == e or destino.startswith(e + "/"):
            return True
    return False


# revisa si el contenido es texto, mirando el Content-Type
def es_contenido_textual(mensaje):
    content_type = (obtener_header(mensaje, "Content-Type") or "").lower()
    if not content_type:
        return False
    if content_type.startswith("text/"):
        return True
    return any(t in content_type for t in ("json", "javascript", "xml", "html"))


# reemplaza las palabras prohibidas dentro del body
def censurar_body(body, forbidden_words):
    resultado = body
    # el reemplazo se hace sobre bytes, asi no importa el encoding de la pagina
    for diccionario in forbidden_words:
        for palabra, reemplazo in diccionario.items():
            resultado = resultado.replace(
                palabra.encode(CODIFICACION_HEADERS), reemplazo.encode(CODIFICACION_HEADERS)
            )
    return resultado


# respuestas que arma el proxy por su cuenta

# arma la respuesta 403 con el html de bloqueo
def respuesta_403(host, path):
    # el <img> hace que el navegador pida la imagen aparte, en un segundo
    # ciclo HTTP, y esa peticion tambien pasa por el proxy
    html = (
        "<!DOCTYPE html>\n"
        "<html lang=\"es\">\n"
        "<head><meta charset=\"utf-8\"><title>403 Forbidden</title></head>\n"
        "<body style=\"font-family:sans-serif;text-align:center\">\n"
        "  <h1>403 Forbidden</h1>\n"
        "  <p>El proxy bloqueo el acceso a <code>{}{}</code>.</p>\n"
        "  <img src=\"/{}\" alt=\"gato guardian\" width=\"220\">\n"
        "  <p>Este sitio esta en la lista de dominios prohibidos.</p>\n"
        "</body>\n</html>\n"
    ).format(host, path, IMAGEN_BLOQUEO)

    body = html.encode("utf-8")
    return create_HTTP_message({
        "es_respuesta": True,
        "version": "HTTP/1.1",
        "codigo_estado": "403",
        "texto_estado": "Forbidden",
        "headers": [
            ["Content-Type", "text/html; charset=utf-8"],
            ["Content-Length", str(len(body))],
            ["Connection", "close"],
        ],
        "body": body,
    })


# lee la imagen del disco y la manda como respuesta
def respuesta_imagen():
    try:
        with open(IMAGEN_BLOQUEO, "rb") as f:
            datos = f.read()
    except OSError:
        # si el archivo no esta, respondemos 404 en vez de caernos
        cuerpo = b"imagen no encontrada"
        return create_HTTP_message({
            "es_respuesta": True, "version": "HTTP/1.1",
            "codigo_estado": "404", "texto_estado": "Not Found",
            "headers": [["Content-Type", "text/plain"],
                        ["Content-Length", str(len(cuerpo))],
                        ["Connection", "close"]],
            "body": cuerpo,
        })

    return create_HTTP_message({
        "es_respuesta": True,
        "version": "HTTP/1.1",
        "codigo_estado": "200",
        "texto_estado": "OK",
        "headers": [
            ["Content-Type", IMAGEN_CONTENT_TYPE],
            ["Content-Length", str(len(datos))],
            ["Connection", "close"],
        ],
        "body": datos,
    })


# respuesta de error generica del proxy
def respuesta_error(codigo, texto, detalle):
    body = "<html><body><h1>{} {}</h1><p>{}</p></body></html>".format(
        codigo, texto, detalle).encode("utf-8")
    return create_HTTP_message({
        "es_respuesta": True, "version": "HTTP/1.1",
        "codigo_estado": str(codigo), "texto_estado": texto,
        "headers": [["Content-Type", "text/html; charset=utf-8"],
                    ["Content-Length", str(len(body))],
                    ["Connection", "close"]],
        "body": body,
    })


# hablar con el servidor real

# le reenvia la peticion al servidor y devuelve su respuesta parseada
def consultar_servidor(peticion, host, puerto, path, usuario, tamano_buffer):
    # al servidor se le manda el path solo, no la URI completa
    peticion["uri"] = path

    # Proxy-Connection es cosa entre el navegador y el proxy, no se reenvia
    borrar_header(peticion, "Proxy-Connection")

    # el header que pide el enunciado
    poner_header(peticion, "X-ElQuePregunta", usuario)

    # pedimos que cierre la conexion, asi sabemos donde termina la respuesta
    poner_header(peticion, "Connection", "close")
    poner_header(peticion, "Host", host if puerto == PUERTO_HTTP_DEFAULT
               else "{}:{}".format(host, puerto))

    # aca el proxy hace de cliente: abre su propio socket hacia el servidor
    socket_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        socket_servidor.connect((host, puerto))
        socket_servidor.send(create_HTTP_message(peticion))
        return recibir_mensaje_HTTP(socket_servidor, tamano_buffer)
    finally:
        socket_servidor.close()


# programa main

def main():
    if len(sys.argv) < 2:
        print("Uso: python3 proxy.py config.json [puerto] [buffer]")
        sys.exit(1)

    # sin esto los print quedan guardados en un buffer y no se ven al momento
    sys.stdout.reconfigure(line_buffering=True)

    # el archivo de configuracion llega como argumento
    with open(sys.argv[1]) as archivo:
        config = json.load(archivo)

    usuario = config["user"]
    blocked = config.get("blocked", [])
    forbidden_words = config.get("forbidden_words", [])
    puerto_proxy = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

    # el buffer se puede pasar por consola para probar distintos tamanos
    buffer_recepcion = int(sys.argv[3]) if len(sys.argv) > 3 else BUFFER_RECEPCION

    # socket que se queda esperando conexiones (HTTP usa sockets con conexion)
    socket_escuchador = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # con esto podemos reiniciar el proxy sin esperar que se libere el puerto
    socket_escuchador.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    socket_escuchador.bind(("0.0.0.0", puerto_proxy))
    socket_escuchador.listen(5)

    print("Proxy escuchando en 0.0.0.0:{}".format(puerto_proxy))
    print("Usuario (X-ElQuePregunta): {}".format(usuario))
    print("Buffer de recepcion: {} bytes".format(buffer_recepcion))
    print("Dominios bloqueados: {}".format(", ".join(blocked)))

    while True:
        # aceptamos una conexion y se crea un socket nuevo para hablar con ella
        socket_cliente, direccion_cliente = socket_escuchador.accept()
        try:
            peticion = recibir_mensaje_HTTP(socket_cliente, buffer_recepcion)
            if peticion is None or peticion.get("es_respuesta"):
                continue

            host, puerto, path = extraer_destino(peticion)
            print("\n[{}:{}] {} {} -> {}:{}{}".format(
                direccion_cliente[0], direccion_cliente[1],
                peticion.get("metodo"), peticion.get("uri"),
                host, puerto, path))

            # 1) si nos piden la imagen del bloqueo, la servimos nosotros
            if path.endswith("/" + IMAGEN_BLOQUEO):
                print("    -> sirviendo imagen local")
                socket_cliente.send(respuesta_imagen())
                continue

            if not host:
                socket_cliente.send(respuesta_error(
                    400, "Bad Request", "No se pudo determinar el host destino."))
                continue

            # 2) si el sitio esta bloqueado, contestamos 403 y no salimos a la red
            if esta_bloqueado(host, path, blocked):
                print("    -> BLOQUEADO (403)")
                socket_cliente.send(respuesta_403(host, path))
                continue

            # 3) si esta permitido, se lo pedimos al servidor real
            try:
                respuesta = consultar_servidor(
                    peticion, host, puerto, path, usuario, buffer_recepcion)
            except socket.gaierror:
                socket_cliente.send(respuesta_error(
                    502, "Bad Gateway", "No se pudo resolver " + host))
                continue
            except OSError as error:
                socket_cliente.send(respuesta_error(
                    502, "Bad Gateway", "Error conectando a {}: {}".format(host, error)))
                continue

            if respuesta is None:
                socket_cliente.send(respuesta_error(
                    502, "Bad Gateway", "El servidor no respondio."))
                continue

            print("    <- {} {} ({} bytes de body)".format(
                respuesta.get("codigo_estado"), respuesta.get("texto_estado"),
                len(respuesta["body"])))

            # 4) censuramos las palabras prohibidas, solo si es texto
            if es_contenido_textual(respuesta) and forbidden_words:
                original = respuesta["body"]
                respuesta["body"] = censurar_body(original, forbidden_words)
                if respuesta["body"] != original:
                    print("    -- palabras censuradas")

            # 5) el Content-Length se recalcula siempre, porque las palabras
            # de reemplazo no miden lo mismo que las originales
            poner_header(respuesta, "Content-Length", str(len(respuesta["body"])))
            poner_header(respuesta, "Connection", "close")

            # 6) le mandamos la respuesta ya modificada al cliente
            socket_cliente.send(create_HTTP_message(respuesta))

        except (OSError, ValueError, UnicodeDecodeError) as error:
            # una peticion mal formada no deberia botar todo el proxy
            print("    !! error atendiendo la peticion: {}".format(error))
        finally:
            socket_cliente.close()


if __name__ == "__main__":
    # atrapamos Ctrl+C para cerrar sin que python muestre el error
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("cerrando el proxy")
