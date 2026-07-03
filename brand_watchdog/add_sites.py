"""Script para adicionar sites SKY+ ao banco de dados."""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from brand_watchdog.config import load_config
from brand_watchdog.models.database import get_session, setup_database, init_db, close_db
from brand_watchdog.models.entities import TargetSiteModel

SKY_PLUS_SITES = [
    "https://powernetsolutions.com.br/",
    "https://www.sejaamigo.com.br/tv-streaming",
    "https://sempreinternet.com.br/",
    "https://www.weclix.com.br/",
    "https://dtel.com.br/home",
    "https://provedorclick.com.br/",
    "https://netjrtelecom.com.br/",
    "https://mundialtelecom.net.br/",
    "https://maxxconectado.com.br/",
    "https://digitalnetms.com.br/",
    "https://www.worldnetfibra.com.br/",
    "https://speedtravel.com.br/",
    "https://www.onnetmais.com.br/",
    "https://futel.com.br/",
    "https://datanetprovedor.com.br/",
    "https://ateltelecom.com.br/",
    "https://worksattelecom.net.br/",
    "https://souuni.com/cidade/alvorada",
    "https://netbox.net.br/",
    "https://bkpnet.com.br/",
    "https://www.qerotelecom.com/",
    "https://prontofibra.com.br/",
    "https://www.teutonet.com.br/",
    "https://norteline.com.br/",
    "https://www.varzeanet.com.br/",
    "https://masterinternetfibra.com.br/",
    "https://ibltelecom.com.br/",
    "https://www.seitel.com.br/",
    "https://www.starmannet.com.br/",
    "https://www.conectnet.net/",
    "https://luzconecct.com/",
    "https://www.sulinternet.net/",
    "https://ivrnet.com.br/",
    "https://espacolink.com.br/",
    "https://www.linkexplorer.com.br/",
    "https://movfibra.com.br/",
    "https://www.netrubifibra.com.br/",
    "https://alcans.com.br/",
    "https://connectlinksp.com.br/",
    "https://www.blinktelecom.com.br/blink-fibra/",
    "https://wspfibra.com.br/inicio/",
    "https://startectelecom.net/",
    "https://ibitelecom.com.br/",
    "https://oxente.net/",
    "https://netaki.com.br/",
    "https://portal.agetelecom.com.br/login",
    "https://seitelbrs.com.br/",
    "https://netcityfibra.com.br/",
    "https://minhadigital.net/",
    "https://www.soudotcom.com/",
    "https://spacelink.net.br/",
    "https://tracecom.net.br/",
    "https://internet58.com.br/",
    "https://aranetplay.com.br/",
    "https://www.mksconnect.com.br/index.php",
    "https://westfibra.com.br/",
    "https://www.ccomtelecom.com.br/",
    "https://www.fibralink.com.br/",
    "https://www.sigafibra.com/",
    "https://www.nedel.com.br/",
    "https://www.infornetfibra.com/",
    "https://wlanfibra.com.br/",
    "https://universotecnologia.com.br/",
    "https://www.athontelecom.com.br/",
    "https://www.redevirtualnet.com.br/",
    "https://contrate.lcitelecom.com.br/",
    "https://bipnet.com.br/",
    "https://www.liveinternetfibra.com.br/",
    "https://netcomfibra.com.br/",
    "https://implantartelecom.com.br/",
    "https://shnetwork.com.br/",
    "https://myconnectpe.com.br/",
    "https://yoofibra.com.br/",
    "https://jhsinternet.com.br/",
    "https://libercom.com.br/",
    "https://www.hardonline.com.br/",
    "https://mkt.masternetrs.com.br/",
    "https://www.3dtelecom.com.br/",
    "https://www.onnetinternet.com.br/",
    "https://maisveloznet.com.br/",
    "https://www.bigfibragv.com.br/",
    "https://www.logininternet.com.br/",
    "https://www.zninternet.com.br/",
    "https://mogifibra.com.br/",
    "https://openitinternet.com.br/",
    "https://brasilink.net.br/",
    "https://n4telecom.com.br/",
    "https://voeinternet.com.br/",
    "https://www.wkve.com.br/",
    "https://olivernettwork.com.br/",
    "https://www.bbgtelecom.com.br/",
    "https://csimaisnet.com.br/",
    "https://www.ramnet.com.br/",
    "https://premiumfibra.com.br/",
    "https://msnetbrasil.com.br/",
    "https://dmsfibra.com.br/",
    "https://sejafibra.net/",
    "https://netcomet.com.br/",
    "https://unafiber.com.br/",
    "https://www.ultranettelecom.com/",
    "https://www.8gfibra.com.br/",
    "https://www.provale.com.br/",
    "https://vntelecom.com.br/",
    "https://www.r2dados.com/",
    "https://mganet.com.br/",
    "https://www.c3telecom.com.br/",
    "https://internetprati.com/",
    "https://torpedotelecom.com.br/",
    "https://www.ultranetpi.com.br/",
    "https://www.dipelnet.com.br/",
    "https://conexaolinkes.com.br/",
    "https://fastprovedor.net.br/",
    "https://omeganetalp.com.br/",
    "https://www.toledofibra.com.br/",
    "https://ibsolfibra.com.br/",
    "https://edeltelecom.com.br/",
    "https://www.digaweb.com.br/",
    "https://ion.com.br/",
    "https://wavemax.com.br/",
    "https://novaportonet.com.br/",
    "https://www.topvianettelecom.com.br/",
    "https://microrcim.tv.br/",
    "https://www.vivanet.net.br/site/index.html",
    "https://wvnfibra.com.br/",
    "https://netmaniainternet.com/",
    "https://viaserver.net.br/",
    "https://etechprovedor.com.br/",
    "https://www.aeroredegoias.com.br/",
    "https://figtelecom.com.br/",
    "https://minhabloom.com.br/",
    "https://reciclanet.com.br/",
    "https://www.ispfibra.com.br/",
    "https://www.araujosat.com.br/",
    "https://www.nortetelfibra.com.br/",
    "https://www.movtelecom.com/",
    "https://www.velloznet.com.br/",
    "https://somosfibra.com.br/",
    "https://itanetfibra.com.br/itapecerica-da-serra/",
    "https://whgsolucoes.com.br/",
    "https://mx3telecom.com.br/",
    "https://wjnet.com.br/",
    "https://netwantelecom.com.br/",
    "https://www.voxxtelecom.com/",
    "https://www.flashnetbrasil.com.br/",
    "https://www.i7telecom.net.br/",
    "https://gonzagatelecom.com.br/",
    "https://navix.com.br/site/",
    "https://www.stratustelecom.com.br/",
    "https://www.sertaolink.net.br/",
    "https://www.redemineira.com.br/",
    "https://sentinternet.com.br/",
    "https://www.viaondas.com.br/",
    "https://fibraplus.com.br/",
    "https://zevo.com.br/",
    "https://neton.net.br/",
    "https://www.uaifibranet.com.br/",
    "https://wittelecom.com.br/",
    "https://www.ontrix.com.br/",
    "https://netcol.com.br/",
    "https://www.ruralconectamg.com.br/",
    "https://www.klinkprovedor.com.br/",
    "https://provedorlive.com.br/",
    "https://www.ancoratelecom.com.br/",
    "https://fenixbrasil.com.br/",
    "https://conexaovip.net.br/",
    "https://www.inovartecnologia.net.br/",
    "https://ragtek.com.br/",
    "https://www.netfasttelecom.net.br/",
    "https://rgcorrea.com.br/",
    "https://centralnetprovedor.com.br/",
    "https://grnettelecom.com.br/",
    "https://orbisnet.com.br/",
    "https://silvernet.net.br/",
    "https://artcompus.com.br/",
    "https://www.ibexinternet.com.br/",
    "https://globonet.net.br/",
    "https://giganetpe.com.br/",
    "https://elinktelecom.com.br/",
    "https://iunitelecom.com.br/",
    "https://www.redeunicon.com.br/",
    "https://www.starttelecom.com.br/",
    "https://www.sounetway.com.br/",
    "https://rupitelecom.com.br/",
    "https://www.agefibra.com.br/",
    "https://clickfibratelecom.com.br/",
    "https://www.dstelecom.net.br/",
    "https://ilhasnet.com.br/",
    "https://sejaleven.com/",
    "https://www.lunefibra.com.br/",
    "https://cjnettelecom.com.br/02/",
    "https://hpsolucoes.com.br/",
    "https://www.linknettelecom.net.br/",
    "https://fenixinternet.com.br/",
    "https://aftelecomprov.com.br/",
    "https://comunet.com.br/",
    "https://atalaianet.com.br/",
    "https://networkevolution.net.br/",
    "https://noisetelecom.com.br/",
    "https://ouronet.com.br/",
    "https://plusat.com.br/",
    "https://01net.com.br/",
    "https://www.portalqueops.com.br/",
    "https://unixinternet.com.br/",
    "https://siqueiranet1.site2.com.br/",
    "https://start.psi.br/",
    "https://www.provedortcnet.com.br/",
    "https://inetradio.com.br/",
    "https://www.invistanet.com.br/",
    "https://cronostelecom.com.br/",
    "https://interlink.net.br/",
    "https://www.inovacaoisp.com.br/",
    "https://www.gmaxfibra.com.br/",
    "https://extremetele.com/",
    "https://maisinternet.net.br/",
    "https://wiupfibra.com/",
    "https://ixc.oneetwo.com/",
    "https://stitelecom.net.br/",
    "https://www.newcentertelecom.com.br/home/",
    "https://v10net.com.br/",
    "https://www.oraclon.com.br/",
    "https://www.rosinternet.com.br/",
    "https://agilseabra.com.br/",
    "https://collis.net.br/",
    "https://dddnet.com.br/",
    "https://www.dinamicatelecom.com.br/",
    "https://www.allrede.com.br/",
    "https://www.faloutelecom.com/",
    "https://americafibra.com.br/",
    "https://netwise.com.br/",
    "https://wianetsul.com.br/",
    "https://wantel.com.br/",
    "https://maxxilink.com.br/",
    "https://stecguaiba.net.br/",
    "https://ultranetoficial.com.br/",
    "https://verticalnet.net.br/",
    "https://westfibertelecomunicacoes.com.br/",
    "https://grupovesc.com.br/",
    "https://primeultraconect.com.br/",
    "https://provedorsettatelecom.com.br/",
    "https://newwebfibra.com.br/",
    "https://uaitelecom.com.br/",
    "https://tconnectfibraoptica.com.br/",
    "https://www.imbranet.com.br/",
    "https://brnet.com.br/",
    "https://everest-telecom.com.br/",
    "https://www.acessosimples.com/",
    "https://www.kingstelecom.com.br/",
    "https://eventelecom.com.br/",
    "https://www.acessenettelecom.com.br/",
    "https://fibra.infonet.com.br/",
    "https://www.almeirimtelecom.com.br/",
    "https://gnetinfo.com.br/",
    "https://f3net.com.br/",
    "https://eternalnet.com.br/",
    "https://hmrtelecom.com/",
    "https://fokusnet.com.br/",
    "https://lpinternet.com.br/",
]


def normalize_url(url: str) -> str:
    """Normaliza URL removendo fragmentos e trailing slash inconsistente."""
    url = url.split("#")[0]  # Remove fragmentos
    return url.lower().rstrip("/")


async def main():
    config = load_config()
    setup_database(config.storage)
    await init_db()

    added = 0
    skipped = 0

    # Deduplicate URLs
    seen_normalized = set()
    unique_sites = []
    for url in SKY_PLUS_SITES:
        norm = normalize_url(url)
        if norm not in seen_normalized:
            seen_normalized.add(norm)
            unique_sites.append(url)

    async with get_session() as session:
        # Get existing normalized URLs
        stmt = select(TargetSiteModel.normalized_url)
        result = await session.execute(stmt)
        existing = {row[0] for row in result.fetchall()}

        for url in unique_sites:
            norm = normalize_url(url)
            if norm in existing:
                skipped += 1
                continue

            site = TargetSiteModel(
                id=str(uuid.uuid4()),
                url=url,
                normalized_url=norm,
                brand="sky_plus",
                active=True,
            )
            session.add(site)
            existing.add(norm)
            added += 1

    await close_db()
    print(f"Sites adicionados: {added}")
    print(f"Sites já existentes (skip): {skipped}")
    print(f"Total no banco: {added + skipped + len(existing) - len(seen_normalized)}")


if __name__ == "__main__":
    asyncio.run(main())
