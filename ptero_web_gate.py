from subprocess import run, check_output
from os import system as runcmd
from os.path import abspath, dirname
import yaml




def iniciar_entorno() -> None:
    runcmd(f"mkdir -p {GLOBAL_VARS['configs-folder']}")
    something_changed = False
    
    for file in GLOBAL_VARS["config-files"].values():
        if (check_output(f"test -f {file['path']} || echo 'false'", shell=True, text=True).replace("\n", "") == "false"):
            runcmd(f"echo \"{file['content'] if file.get('content', None) else ''}\" > {file['path']}")
            something_changed = True

    return something_changed


def cargar_servicios(file_path: str) -> None:
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
        GLOBAL_VARS["services"] = data['sites'] if data else list()


def run_cmd(command: str) -> dict[str, str | list]:
    result = run(command, shell=True, capture_output=True, text=True)

    return {
        "status": "success" if result.returncode == 0 else "fail",
        "result": result.stdout.split("\n") if result.returncode == 0 else result.stderr,
    }


def service_port_in_source(source_data: list[str], service_port: str) -> bool:
    port_found = False
    idx = 0

    while idx < len(source_data) and not port_found:
        port_found = True if service_port in source_data[idx] else False
        idx += 1

    result = {
        "found": port_found,
    }

    if port_found: result["at"] = (idx - 1)
    return result 


def get_data_from_iptables(chain: str) -> dict[str, str | list]:
    return run_cmd(f"iptables -S {chain}")


def get_data_from_iptables_rule(iptables_data: list[str], service_port: str) -> dict[str, str]:
    service_search = service_port_in_source(iptables_data, service_port)
    if not service_search["found"]:
        return {
            "status": "fail",
            "reason": "No se encontró el puerto del servicio dentro del firewall."
        }
    
    iptables_rule = iptables_data[service_search["at"]]
    return {
        "status": "success",
        "ip": iptables_rule.split("-d ")[1].split("/")[0],
        "port": service_port,
    }
    

def get_data_from_webconfig(config_fullpath: str) -> dict[str, str | list]:
    result = run_cmd(f"grep 'proxy_pass' {config_fullpath}")
    result_data = result["result"][0]
    
    if result_data.replace(" ", "") == "":
        result["status"] == "fail"

    if result["status"] != "success": return result
    web_data = result_data.split("://")[1].replace(";", "").split(":")

    return {
        "status": "success",
        "ip": web_data[0],
        "port": web_data[1],
    }


def existe_archivo(path: str) -> bool:
    return not check_output(f"test -f {path} || echo 'false'", shell=True, text=True).replace("\n", "") == "false"




def main() -> None:
    services_list = GLOBAL_VARS["services"]
    any_service_updated = False

    if len(services_list) <= 0:
        print("WARN: No hay ningún sitio configurado, el script finalizará.")
        return

    iptables_data = get_data_from_iptables(GLOBAL_VARS["firewall-chain"])
    if iptables_data["status"] == "fail":
        print("ERROR: Hubo un error al intentar obtener las reglas del firewall.")
        return

    for service in services_list:
        service_iptables_data = get_data_from_iptables_rule(iptables_data["result"], str(service["port"]))
        service_webconfig_data = get_data_from_webconfig(service["webconfig-path"])

        if service_iptables_data["status"] == "fail":
            print(f"ERROR: Hubo un error al intentar obtener la información del servicio \"{service['name']}\" en el firewall.")
            print(service_iptables_data['reason'])
            continue

        if service_webconfig_data["status"] == "fail":
            print(f"ERROR: Hubo un error al intentar obtener la información del servicio \"{service['name']}\" en su fichero de configuración del servidor web.")
            continue

        service_iptables_ip = service_iptables_data["ip"]
        service_webconfig_ip = service_webconfig_data["ip"]

        if service_iptables_ip == service_webconfig_ip:
            print(f"INFO: El servicio \"{service['name']}\" no ha cambiado de IP.")
            continue

        if not any_service_updated:
            any_service_updated = True

        if not existe_archivo(service["webconfig-path"]):
            print(f"ERROR: El fichero de configuración en el servidor web del servicio \"{service['name']}\" no existe.")

        with open(service["webconfig-path"], "tr+") as file:
            line = ""

            while "proxy_pass" not in line:
                line = file.readline()

            file.seek(file.tell() - len(line))
            file.write(f"        proxy_pass http://{service_iptables_ip}:{service['port']};")

            print("SUCCESS: La ip del servicio se ha cambiado correctamente en el fichero de configuración del servidor web.")

    if any_service_updated:
        result = run_cmd("systemctl reload nginx")
        if result["status"] != "success":
            print("ERROR: Hubo un problema al intentar recargar nginx.")
            return
        
        print("SUCCESS: Nginx se recargo satisfactoriamente.")




if __name__ == "__main__":
    GLOBAL_VARS = {
        "running-path": dirname(abspath(__file__)),
        "firewall-chain": "DOCKER",
        "services": [],
    }

    GLOBAL_VARS["configs-folder"] = f"{GLOBAL_VARS['running-path']}/configs"
    GLOBAL_VARS["config-files"] = {
        "sites-config": {
            "path": f"{GLOBAL_VARS['configs-folder']}/sites.yml",
            "content": "sites:\n  - name: \"test01\"\n    webconfig-path: \"/etc/nginx/sites-available/test01.conf\"\n    port: \"5000\"\n\n  - name: \"test02\"\n    webconfig-path: \"/etc/nginx/sites-available/test02.conf\"\n    port: \"6000\"",
        },
    }

    any_change = iniciar_entorno()
    if any_change:
        print("INFO: La herramienta se ha iniciado correctamente. ¡CONFIGURALA!")
    else:
        cargar_servicios(GLOBAL_VARS["config-files"]["sites-config"]["path"])
        main()


# Poner para que se pueda inicializar la herramienta con argumentos, y que se pueda ejecutar por separado, segun los argumentos.