import streamlit as st
import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import Point
import fiona
import random
import io
import folium
from streamlit_folium import st_folium

# Configuração de drivers KML
fiona.drvsupport.supported_drivers['KML'] = 'rw'

st.set_page_config(page_title="Auditoria Rodoviária", layout="wide")

st.title("🚧 Auditoria: Amostragem Sequencial (IBRAOP)")
st.markdown("Sequência fixa: **Direito ➔ Eixo ➔ Esquerdo**.")

# --- SIDEBAR ---
st.sidebar.header("Parâmetros Técnicos")
uploaded_file = st.sidebar.file_uploader("Carregue o KML da Rodovia", type=['kml'])
largura = st.sidebar.number_input("Largura da pista (m)", value=7.0, step=0.5)
area_min = st.sidebar.number_input("Área mínima por amostra (m²) - Norma", value=7000.0, step=100.0)
qtd_desejada = st.sidebar.number_input("Quantidade de amostras pretendida", value=50, step=1)
dist_min = st.sidebar.number_input("Distância mínima entre pontos (m)", value=320.0, step=10.0)

def identificar_zonas_curvas(linha, recuo=150):
    zonas = []
    passo = 10
    try:
        for d in range(passo, int(linha.length) - passo, passo):
            p1, p2, p3 = linha.interpolate(d-passo), linha.interpolate(d), linha.interpolate(d+passo)
            v1 = np.array([p2.x-p1.x, p2.y-p1.y])
            v2 = np.array([p3.x-p2.x, p3.y-p2.y])
            norm = (np.linalg.norm(v1) * np.linalg.norm(v2))
            if norm != 0 and (np.dot(v1, v2)/norm) < 0.9995:
                zonas.append((d - recuo, d + recuo))
    except: pass
    return zonas

def gerar_pontos(linha, n_pontos, dist_min_m, zonas_proibidas, largura_p, utm_crs):
    amostras_temp = []
    tentativas = 0
    extensao = linha.length
    
    while len(amostras_temp) < n_pontos and tentativas < 60000:
        dist = random.uniform(0, extensao)
        esta_proibido = any(i <= dist <= f for i, f in zonas_proibidas)
        if not esta_proibido:
            if all(abs(dist - a['dist']) >= dist_min_m for a in amostras_temp):
                amostras_temp.append({'dist': dist})
        tentativas += 1

    amostras_temp.sort(key=lambda x: x['dist'])
    sequencia_bordos = ["Bordo Direito", "Eixo", "Bordo Esquerdo"]
    dados = []
    
    for i, amos in enumerate(amostras_temp):
        bordo = sequencia_bordos[i % 3]
        offset = (largura_p/2) if bordo == "Bordo Direito" else (-(largura_p/2) if bordo == "Bordo Esquerdo" else 0)
        p1, p2 = linha.interpolate(amos['dist']), linha.interpolate(amos['dist'] + 0.1)
        mag = np.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2)
        ponto_geom = Point(p1.x - (p2.y - p1.y)/mag * offset, p1.y + (p2.x - p1.x)/mag * offset)
        ponto_wgs84 = gpd.GeoSeries([ponto_geom], crs=utm_crs).to_crs(epsg=4326)[0]
        
        dados.append({
            'Amostra': i + 1, 'Identificação': f"Amostra {i+1:02d}",
            'Posição Lateral': bordo, 'Quilometragem': f"km {amos['dist']/1000:.3f}",
            'Latitude': ponto_wgs84.y, 'Longitude': ponto_wgs84.x, 
            'geometry': ponto_geom, 'crs_origem': utm_crs
        })
    return pd.DataFrame(dados)

# --- LÓGICA PRINCIPAL ---
if uploaded_file:
    # 1. Carregamento Inicial
    gdf = gpd.read_file(uploaded_file, driver='KML')
    utm_gdf = gdf.to_crs(gdf.estimate_utm_crs())
    linha_rodovia = utm_gdf.geometry.iloc[0]
    
    # 2. Cálculo do Mínimo IBRAOP
    extensao_total = linha_rodovia.length
    n_minimo_ibraop = int(np.ceil((extensao_total * largura) / area_min))
    
    st.info(f"📏 Extensão detectada: {extensao_total/1000:.2f} km | Mínimo requerido (IBRAOP): **{n_minimo_ibraop} amostras**")

    # Inicializa variáveis de controle
    executar_geracao = False
    n_final = qtd_desejada

    # 3. Verificação de Alerta
    if qtd_desejada < n_minimo_ibraop:
        st.warning(f"⚠️ **ALERTA TÉCNICO:** A quantidade solicitada ({qtd_desejada}) é inferior ao mínimo requerido pela norma IBRAOP ({n_minimo_ibraop}).")
        
        col_btn1, col_btn2 = st.columns(2)
        if col_btn1.button(f"Corrigir para Mínimo ({n_minimo_ibraop})"):
            n_final = n_minimo_ibraop
            executar_geracao = True
        if col_btn2.button(f"Prosseguir com {qtd_desejada} (Ciente do risco)"):
            n_final = qtd_desejada
            executar_geracao = True
    else:
        if st.sidebar.button("Gerar Amostras"):
            executar_geracao = True

    # 4. Execução e Persistência
    if executar_geracao:
        with st.spinner('Processando geometria...'):
            zonas = identificar_zonas_curvas(linha_rodovia)
            df_gerado = gerar_pontos(linha_rodovia, n_final, dist_min, zonas, largura, utm_gdf.crs.to_string())
            st.session_state['amostras'] = df_gerado

    # 5. Exibição dos Resultados (Se existirem na memória)
    if st.session_state.get('amostras') is not None:
        df_final = st.session_state['amostras']
        
        st.subheader(f"📋 Tabela de Amostragem ({len(df_final)} pontos)")
        st.dataframe(df_final.drop(columns=['geometry', 'crs_origem']), use_container_width=True)

        # Mapa
        centro = [df_final.Latitude.mean(), df_final.Longitude.mean()]
        m = folium.Map(location=centro, zoom_start=13)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='Google Satellite').add_to(m)
        cores = {"Bordo Direito": "red", "Eixo": "blue", "Bordo Esquerdo": "green"}
        for _, row in df_final.iterrows():
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']], radius=6, 
                color=cores[row['Posição Lateral']], fill=True, fill_opacity=0.8,
                popup=f"{row['Identificação']}: {row['Posição Lateral']}"
            ).add_to(m)
        st_folium(m, width=1100, height=500, returned_objects=[])

        # Downloads
        c1, c2 = st.columns(2)
        
        # KML
        try:
            crs_orig = df_final['crs_origem'].iloc[0]
            amostras_gdf = gpd.GeoDataFrame(df_final, geometry='geometry', crs=crs_orig).to_crs(epsg=4326)
            amostras_gdf['Name'] = amostras_gdf['Identificação'] + " - " + amostras_gdf['Posição Lateral']
            buf_kml = io.BytesIO()
            amostras_gdf[['Name', 'geometry']].to_file(buf_kml, driver='KML')
            c1.download_button("📥 Baixar KML", buf_kml.getvalue(), "amostras.kml")
        except: c1.error("Erro no KML")

        # Excel
        try:
            buf_xlsx = io.BytesIO()
            with pd.ExcelWriter(buf_xlsx, engine='openpyxl') as writer:
                df_final.drop(columns=['geometry', 'crs_origem']).to_excel(writer, index=False)
            c2.download_button("📥 Baixar Excel", buf_xlsx.getvalue(), "amostras.xlsx")
        except: c2.error("Erro no Excel")
