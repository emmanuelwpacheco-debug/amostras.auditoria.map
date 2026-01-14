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

# Habilita drivers KML
fiona.drvsupport.supported_drivers['KML'] = 'rw'

st.set_page_config(page_title="Auditoria Rodoviária", layout="wide")

st.title("🚧 Auditoria: Amostragem com Mapa Interativo")
st.markdown("Sequência fixa: **Direito ➔ Eixo ➔ Esquerdo**.")

# --- SIDEBAR ---
st.sidebar.header("Parâmetros Técnicos")
uploaded_file = st.sidebar.file_uploader("Carregue o KML da Rodovia", type=['kml'])
largura = st.sidebar.number_input("Largura da pista (m)", value=7.0, step=0.5)
area_min = st.sidebar.number_input("Área mínima por amostra (m²)", value=7000.0, step=100.0)
qtd_desejada = st.sidebar.number_input("Quantidade pretendida", value=50, step=1)
dist_min = st.sidebar.number_input("Distância mínima (m)", value=320.0, step=10.0)

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
    except:
        pass
    return zonas

if uploaded_file:
    if 'amostras' not in st.session_state:
        st.session_state['amostras'] = None

    if st.sidebar.button("Gerar Amostras"):
        with st.spinner('Calculando pontos e gerando mapa...'):
            gdf = gpd.read_file(uploaded_file, driver='KML')
            utm_gdf = gdf.to_crs(gdf.estimate_utm_crs())
            linha_rodovia = utm_gdf.geometry.iloc[0]
            extensao = linha_rodovia.length
            
            n_minimo = int(np.ceil((extensao * largura) / area_min))
            n_final = max(qtd_desejada, n_minimo)

            zonas_proibidas = identificar_zonas_curvas(linha_rodovia)
            amostras_temp = []
            tentativas = 0
            
            while len(amostras_temp) < n_final and tentativas < 50000:
                dist = random.uniform(0, extensao)
                esta_proibido = any(i <= dist <= f for i, f in zonas_proibidas)
                if not esta_proibido:
                    if all(abs(dist - a['dist']) >= dist_min for a in amostras_temp):
                        amostras_temp.append({'dist': dist})
                tentativas += 1

            amostras_temp.sort(key=lambda x: x['dist'])
            sequencia_bordos = ["Bordo Direito", "Eixo", "Bordo Esquerdo"]
            dados_finais = []
            
            for i, amos in enumerate(amostras_temp):
                bordo = sequencia_bordos[i % 3]
                offset = (largura/2) if bordo == "Bordo Direito" else (-(largura/2) if bordo == "Bordo Esquerdo" else 0)
                p1, p2 = linha_rodovia.interpolate(amos['dist']), linha_rodovia.interpolate(amos['dist'] + 0.1)
                mag = np.sqrt((p2.x - p1.x)**2 + (p2.y - p1.y)**2)
                ponto_geom = Point(p1.x - (p2.y - p1.y)/mag * offset, p1.y + (p2.x - p1.x)/mag * offset)
                ponto_wgs84 = gpd.GeoSeries([ponto_geom], crs=utm_gdf.crs).to_crs(epsg=4326)[0]
                
                dados_finais.append({
                    'Amostra': i + 1, 'Identificação': f"Amostra {i+1:02d}",
                    'Posição Lateral': bordo, 'Quilometragem': f"km {amos['dist']/1000:.3f}",
                    'Latitude': ponto_wgs84.y, 'Longitude': ponto_wgs84.x, 
                    'geometry': ponto_geom, 'crs_origem': utm_gdf.crs.to_string()
                })

            st.session_state['amostras'] = pd.DataFrame(dados_finais)

    if st.session_state['amostras'] is not None:
        df_final = st.session_state['amostras']
        st.subheader("📋 Tabela de Amostragem")
        st.dataframe(df_final.drop(columns=['geometry', 'crs_origem']), use_container_width=True)

        st.subheader("🗺️ Visualização Espacial")
        m = folium.Map(location=[df_final.Latitude.mean(), df_final.Longitude.mean()], zoom_start=13)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='Google Satellite').add_to(m)
        cores = {"Bordo Direito": "red", "Eixo": "blue", "Bordo Esquerdo": "green"}
        for _, row in df_final.iterrows():
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=5, color=cores[row['Posição Lateral']], fill=True,
                popup=f"{row['Identificação']}: {row['Posição Lateral']}"
            ).add_to(m)
        st_folium(m, width=1100, height=500, returned_objects=[])

        col1, col2 = st.columns(2)
        
        # Download KML
        crs_origem = df_final['crs_origem'].iloc[0]
        amostras_gdf = gpd.GeoDataFrame(df_final, geometry='geometry', crs=crs_origem).to_crs(epsg=4326)
        amostras_gdf['Name'] = amostras_gdf['Identificação'] + " - " + amostras_gdf['Posição Lateral']
        buffer_kml = io.BytesIO()
        amostras_gdf[['Name', 'geometry']].to_file(buffer_kml, driver='KML')
        col1.download_button("📥 Baixar KML", buffer_kml.getvalue(), "amostras.kml", key="kml_btn")

        # Download Excel
        buffer_excel = io.BytesIO()
        with pd.ExcelWriter(buffer_excel, engine='openpyxl') as writer:
            df_final.drop(columns=['geometry', 'crs_origem']).to_excel(writer, index=False)
        col2.download_button("📥 Baixar Excel", buffer_excel.getvalue(), "amostras.xlsx", key="xlsx_btn")
