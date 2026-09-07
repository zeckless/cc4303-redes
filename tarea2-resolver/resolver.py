# CC4303 Redes - Actividad 2: construyamos un resolver DNS
# Mario Opazo - Seccion 1
# Profesora: Ivana Bachmann

import socket
import sys
from collections import Counter

from dnslib import DNSRecord, QTYPE

# servidor raiz que indica el enunciado
IP_RAIZ = "198.41.0.4"

# los servidores DNS reales escuchan en el 53
PUERTO_DNS = 53

# nuestro resolver usa el 8000 porque el 53 es un puerto reservado
# y necesitaria permisos de root
PUERTO_RESOLVER = 8000

# si un name server no responde, no queremos quedarnos esperando para siempre
TIMEOUT_RESPUESTA = 3

# tope de saltos, para no quedar en un ciclo infinito si una delegacion
# apunta de vuelta a donde ya estuvimos
MAX_PROFUNDIDAD = 12

# buffer grande: el enunciado dice que en esta actividad no hay que
# preocuparse de mensajes mas grandes que el buffer
BUFFER_DNS = 4096

# el cache guarda los 3 dominios mas repetidos de las ultimas 20 consultas
HISTORIAL_MAX = 20
TOP_CACHE = 3

DEBUG = True


# imprime solo si el modo debug esta activo
def imprimir_debug(mensaje):
    if DEBUG:
        print("(debug) " + mensaje, flush=True)


# mensajes DNS

# toma un mensaje DNS en bytes y lo deja en un diccionario
def parsear_mensaje_dns(data):
    registro = DNSRecord.parse(data)
    # guardamos los registros como objetos de dnslib y no como texto, porque
    # despues hay que preguntarles el tipo para decidir el siguiente paso
    return {
        "qname": str(registro.q.qname),
        "qtype": QTYPE.get(registro.q.qtype),
        "id": registro.header.id,
        "ancount": registro.header.a,
        "nscount": registro.header.auth,
        "arcount": registro.header.ar,
        "answer": registro.rr,
        "authority": registro.auth,
        "additional": registro.ar,
        "registro": registro,
    }


# de una lista de registros devuelve solo los del tipo que pedimos (ej: "A")
def buscar_tipo(registros, tipo):
    # hay que filtrar porque una misma seccion mezcla tipos distintos:
    # dig manda un registro OPT en Additional que no es una IP
    return [rr for rr in registros if QTYPE.get(rr.rtype) == tipo]


# consultas a los name servers

# manda un mensaje DNS a un name server y devuelve lo que responda
def enviar_query(mensaje, ip_destino):
    # DNS usa sockets no orientados a conexion, asi que van sendto y recvfrom
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT_RESPUESTA)
    try:
        sock.sendto(mensaje, (ip_destino, PUERTO_DNS))
        respuesta, _ = sock.recvfrom(BUFFER_DNS)
        return respuesta
    except (socket.timeout, OSError):
        # si el servidor no contesta devolvemos None en vez de caernos
        return None
    finally:
        sock.close()


# resolucion

# resuelve una consulta bajando por la jerarquia DNS
# devuelve el mensaje de respuesta en bytes, no solo la IP, porque dig
# espera un mensaje DNS completo
def resolver(mensaje_consulta, ip_addr=IP_RAIZ, nombre_ns=".", profundidad=0):
    if profundidad > MAX_PROFUNDIDAD:
        imprimir_debug("se alcanzo la profundidad maxima de delegaciones, se aborta")
        return None

    consulta = parsear_mensaje_dns(mensaje_consulta)
    imprimir_debug("Consultando '{}' a '{}' con direccion IP '{}'".format(
        consulta["qname"], nombre_ns, ip_addr))

    # a) le mandamos la consulta al servidor que toca en este paso
    respuesta_bytes = enviar_query(mensaje_consulta, ip_addr)
    if respuesta_bytes is None:
        imprimir_debug("sin respuesta de {} (timeout)".format(ip_addr))
        return None

    respuesta = parsear_mensaje_dns(respuesta_bytes)

    # b) si ya viene un registro A en Answer, terminamos
    if buscar_tipo(respuesta["answer"], "A"):
        imprimir_debug("respuesta encontrada para '{}'".format(consulta["qname"]))
        return respuesta_bytes

    # c) si viene una delegacion (registros NS en Authority) hay que bajar
    registros_ns = buscar_tipo(respuesta["authority"], "NS")
    if registros_ns:
        # c.i) si en Additional viene la IP del proximo servidor, la usamos
        # directamente y no hay que resolver nada aparte
        glue = buscar_tipo(respuesta["additional"], "A")
        if glue:
            siguiente_ip = str(glue[0].rdata)
            siguiente_nombre = str(glue[0].rname)
            return resolver(mensaje_consulta, siguiente_ip,
                            siguiente_nombre, profundidad + 1)

        # c.ii) si no viene la IP, tenemos que averiguarla primero:
        # dejamos la consulta original en pausa y resolvemos el nombre
        # del name server empezando otra vez desde la raiz
        nombre_siguiente_ns = str(registros_ns[0].rdata)
        imprimir_debug("delegacion sin glue: primero hay que resolver '{}'".format(
            nombre_siguiente_ns))

        consulta_ns = DNSRecord.question(nombre_siguiente_ns)
        respuesta_ns = resolver(bytes(consulta_ns.pack()), IP_RAIZ, ".",
                                profundidad + 1)
        if respuesta_ns is None:
            imprimir_debug("no se pudo resolver la IP de '{}'".format(nombre_siguiente_ns))
            return None

        registros_a = buscar_tipo(parsear_mensaje_dns(respuesta_ns)["answer"], "A")
        if not registros_a:
            return None

        # ya tenemos la IP del name server, retomamos la consulta original
        ip_siguiente_ns = str(registros_a[0].rdata)
        return resolver(mensaje_consulta, ip_siguiente_ns,
                        nombre_siguiente_ns, profundidad + 1)

    # d) cualquier otra respuesta se ignora (por ejemplo si solo viene un SOA
    # o solo un CNAME que apunta fuera de esta zona)
    # el problema es que asi el cliente no recibe nada y le aparece un timeout
    imprimir_debug("respuesta sin A ni delegacion NS: se ignora "
                   "(ancount={}, nscount={}, arcount={})".format(
                       respuesta["ancount"], respuesta["nscount"],
                       respuesta["arcount"]))
    return None


# cache

# guarda las respuestas de los dominios que mas se repiten
class CacheDNS:

    def __init__(self):
        # lista con los ultimos 20 dominios consultados, en orden.
        # es lista y no set porque justamente nos interesan las repeticiones
        self.historial = []
        # diccionario dominio -> respuesta en bytes, para acceso directo
        self.respuestas = {}

    # agrega la consulta al historial y lo deja con 20 elementos como maximo
    def registrar_consulta(self, qname):
        self.historial.append(qname)
        if len(self.historial) > HISTORIAL_MAX:
            self.historial.pop(0)

    # los 3 dominios que mas aparecen en el historial
    def top_dominios(self):
        return [nombre for nombre, _ in Counter(self.historial).most_common(TOP_CACHE)]

    # guardamos la respuesta de todo dominio que resolvemos, no solo de los
    # del top 3, porque un dominio puede entrar al top mas adelante
    def guardar(self, qname, respuesta_bytes):
        self.respuestas[qname] = respuesta_bytes

    # devuelve la respuesta guardada si el dominio esta en el top 3
    def obtener(self, qname):
        if qname in self.top_dominios() and qname in self.respuestas:
            return self.respuestas[qname]
        return None


# le cambia el ID a una respuesta guardada en cache
def adaptar_respuesta_cacheada(respuesta_bytes, id_consulta):
    # cada consulta lleva un ID y la respuesta tiene que repetirlo. la
    # respuesta guardada trae el ID de la consulta vieja, asi que si la
    # mandamos tal cual dig la descarta y muestra un timeout
    registro = DNSRecord.parse(respuesta_bytes)
    registro.header.id = id_consulta
    return bytes(registro.pack())


# programa main

def main():
    global DEBUG

    puerto = PUERTO_RESOLVER
    for argumento in sys.argv[1:]:
        if argumento == "--no-debug":
            DEBUG = False
        elif argumento == "--debug":
            DEBUG = True
        else:
            puerto = int(argumento)

    # sin esto los print no se ven al momento
    sys.stdout.reconfigure(line_buffering=True)

    # socket no orientado a conexion: no hay listen ni accept, el mismo
    # socket recibe y responde a todos los clientes
    socket_resolver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    socket_resolver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    socket_resolver.bind(("0.0.0.0", puerto))

    cache = CacheDNS()

    print("Resolver DNS escuchando en 0.0.0.0:{}".format(puerto))
    print("Servidor raiz: {}".format(IP_RAIZ))
    print("Cache: top {} dominios de las ultimas {} consultas".format(
        TOP_CACHE, HISTORIAL_MAX))
    print("Modo debug: {}".format("activo" if DEBUG else "inactivo"))

    while True:
        mensaje_consulta, direccion_cliente = socket_resolver.recvfrom(BUFFER_DNS)

        try:
            consulta = parsear_mensaje_dns(mensaje_consulta)
        except Exception as error:
            print("mensaje ilegible desde {}: {}".format(direccion_cliente, error))
            continue

        qname = consulta["qname"]
        print("\n>> consulta de {}: {} {}".format(
            direccion_cliente, qname, consulta["qtype"]))

        # anotamos la consulta antes de revisar el cache, para que la segunda
        # consulta a un mismo dominio ya lo vea con 2 apariciones
        cache.registrar_consulta(qname)

        respuesta = cache.obtener(qname)
        if respuesta is not None:
            # estaba en el cache, no salimos a la red
            imprimir_debug("'{}' respondido DESDE EL CACHE (sin consultar la red)".format(qname))
            respuesta = adaptar_respuesta_cacheada(respuesta, consulta["id"])
        else:
            # no estaba, hay que resolverlo bajando desde la raiz
            imprimir_debug("'{}' no esta en el cache, se resuelve desde la raiz".format(qname))
            respuesta = resolver(mensaje_consulta)
            if respuesta is not None:
                cache.guardar(qname, respuesta)

        if respuesta is not None:
            socket_resolver.sendto(respuesta, direccion_cliente)
            print("<< respondido ({} bytes)".format(len(respuesta)))
        else:
            # no mandamos nada, el cliente va a ver un timeout
            print("<< sin respuesta para {}".format(qname))


if __name__ == "__main__":
    # atrapamos Ctrl+C para cerrar sin que python muestre el error
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("cerrando el resolver")
