import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import pickle
import os

MODEL_PATH = "flood_model.pkl"

def generate_training_data(n_samples=5000):
    """
    Generate synthetic data representing flood historical records.
    Features: elevation (m), distance_to_water (m), rainfall (mm), 
              sar_water_presence (ratio), soil_moisture (0-1)
    """
    np.random.seed(42)
    # Elevation: mostly low lying (0 to 15m)
    elevation = np.random.uniform(0, 15, n_samples)
    # Distance to water: 0 to 5000m
    distance_to_water = np.random.uniform(0, 5000, n_samples)
    # Rainfall: 0 to 400mm
    rainfall = np.random.uniform(0, 400, n_samples)
    # SAR live water detection (0 to 1 ratio area flooded)
    sar_water_presence = np.random.beta(0.5, 0.5, n_samples) 
    # Soil moisture (0 to 1)
    soil_moisture = np.clip(np.random.normal(0.6, 0.2, n_samples), 0, 1)
    
    # Calculate flood depth based on complex physics-inspired heuristic
    # Exponential decay for distance to water
    river_overflow = np.exp(-distance_to_water / 1000) * (rainfall * 0.05)
    
    # Base height
    water_level = river_overflow + (rainfall * soil_moisture * 0.02)
    
    # SAR impact: If satellite sees water, it drastically increases depth
    water_level += (sar_water_presence * 2.0)
    
    # Final depth
    flood_depth = water_level - elevation + 1.0
    flood_depth = np.maximum(0, flood_depth) # ReLU
    
    # Add noise to simulate variance
    noise = np.random.normal(0, 0.2, n_samples)
    flood_depth = np.maximum(0, flood_depth + noise)
    
    df = pd.DataFrame({
        'elevation': elevation,
        'distance_to_water': distance_to_water,
        'rainfall': rainfall,
        'sar_water_presence': sar_water_presence,
        'soil_moisture': soil_moisture,
        'flood_depth': flood_depth
    })
    return df

def train_and_save_model():
    print("Generating advanced training data...")
    df = generate_training_data()
    X = df[['elevation', 'distance_to_water', 'rainfall', 'sar_water_presence', 'soil_moisture']]
    y = df['flood_depth']
    
    print("Training Gradient Boosting model pipeline...")
    pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('gbm', GradientBoostingRegressor(n_estimators=150, learning_rate=0.1, max_depth=5, random_state=42))
    ])
    pipeline.fit(X, y)
    
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(pipeline, f)
    print("Model saved to", MODEL_PATH)

def predict_flood_depths(features):
    """
    features: list of dicts [{'elevation': e, 'distance_to_water': d, 'rainfall': r, 'sar_water_presence': s, 'soil_moisture': sm}, ...]
    returns list of predicted depths
    """
    # If the file exists but we recently changed the model architecture in codebase, 
    # it might load the old Random Forest if we don't handle it. Force retraining if file size or date doesn't match?
    # For now, if Random Forest is there, it will crash when accessing scaler, so better to delete the pkl.
    try:
        with open(MODEL_PATH, "rb") as f:
            pipeline = pickle.load(f)
            # test if it's the new pipeline
            _ = pipeline.predict(pd.DataFrame([features[0]]))
    except Exception:
        print("Model out of date or missing, retraining...")
        train_and_save_model()
        with open(MODEL_PATH, "rb") as f:
            pipeline = pickle.load(f)
            
    df = pd.DataFrame(features)
    # Ensure columns match training order exactly
    df = df[['elevation', 'distance_to_water', 'rainfall', 'sar_water_presence', 'soil_moisture']]
    predictions = pipeline.predict(df)
    return predictions

if __name__ == "__main__":
    train_and_save_model()
