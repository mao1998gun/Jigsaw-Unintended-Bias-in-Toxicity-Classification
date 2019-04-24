from string import punctuation
import numpy as np
import pandas as pd
import os
from keras.models import Model
from keras.layers import Input, Dense, Embedding, SpatialDropout1D, Dropout, add, concatenate
from keras.layers import CuDNNLSTM, Bidirectional, GlobalMaxPooling1D, GlobalAveragePooling1D
from keras.preprocessing import text, sequence
from keras.callbacks import LearningRateScheduler

os.environ["CUDA_VISIBLE_DEVICES"]="0,1"

NUM_MODELS = 2
BATCH_SIZE = 512
LSTM_UNITS = 128
DENSE_HIDDEN_UNITS = 4 * LSTM_UNITS
EPOCHS = 4
MAX_LEN = 220

EMBEDDING_FILES = [
    'embedding/crawl-300d-2M.vec',
    'embedding/glove.6B.300d.txt'
]


def preprocess(data):

    def clean_special_chars(text, punct):
        for p in punctuation:
            text = text.replace(p, ' ')
        return text

    data = data.astype(str).apply(lambda x: clean_special_chars(x, punctuation))
    return data


def get_coefs(word, *arr):
    return word, np.asarray(arr, dtype='float32')


def load_embeddings(path):
    with open(path, encoding='utf-8') as f:
        return dict(get_coefs(*line.strip().split(' ')) for line in f)

def build_model(embedding_matrix, num_aux_targets):
    words = Input(shape=(MAX_LEN,))
    x = Embedding(*embedding_matrix.shape, weights=[embedding_matrix], trainable=False)(words)
    x = SpatialDropout1D(0.3)(x)
    x = Bidirectional(CuDNNLSTM(LSTM_UNITS, return_sequences=True))(x)
    x = Bidirectional(CuDNNLSTM(LSTM_UNITS, return_sequences=True))(x)

    hidden = concatenate([
        GlobalMaxPooling1D()(x),
        GlobalAveragePooling1D()(x),
    ])
    hidden = add([hidden, Dense(DENSE_HIDDEN_UNITS, activation='relu')(hidden)])
    hidden = add([hidden, Dense(DENSE_HIDDEN_UNITS, activation='relu')(hidden)])
    result = Dense(1, activation='sigmoid')(hidden)
    aux_result = Dense(num_aux_targets, activation='sigmoid')(hidden)

    model = Model(inputs=words, outputs=[result, aux_result])
    model.compile(loss='binary_crossentropy', optimizer='adam')

    return model


def build_matrix(word_index, path):
    embedding_index = load_embeddings(path)
    embedding_matrix = np.zeros((len(word_index) + 1, 300))
    for word, i in word_index.items():
        try:
            embedding_matrix[i] = embedding_index[word]
        except KeyError:
            pass
    return embedding_matrix


def main():
    print('start load data...')
    train = pd.read_csv('./csv/train.csv')
    test = pd.read_csv('./csv/test.csv')
    print('find %d train dataset and %d test dataset' %(len(train), len(train)))

    x_train = preprocess(train['comment_text'])
    y_train = np.where(train['target'] >= 0.5, 1, 0)
    y_aux_train = train[['target', 'severe_toxicity', 'obscene', 'identity_attack', 'insult', 'threat']]
    x_test = preprocess(test['comment_text'])

    tokenizer = text.Tokenizer()
    tokenizer.fit_on_texts(list(x_train) + list(x_test))

    x_train = tokenizer.texts_to_sequences(x_train)
    x_test = tokenizer.texts_to_sequences(x_test)
    x_train = sequence.pad_sequences(x_train, maxlen=MAX_LEN)
    x_test = sequence.pad_sequences(x_test, maxlen=MAX_LEN)

    print('load embedding...')
    embedding_matrix = np.concatenate([build_matrix(tokenizer.word_index, f) for f in EMBEDDING_FILES], axis=-1)


    checkpoint_predictions = []
    weights = []

    print('start train...')
    for model_idx in range(NUM_MODELS):
        model = build_model(embedding_matrix, y_aux_train.shape[-1])
        for global_epoch in range(EPOCHS):
            model.fit(
                x_train,
                [y_train, y_aux_train],
                batch_size=BATCH_SIZE,
                epochs=1,
                verbose=2,
                callbacks=[
                    LearningRateScheduler(lambda epoch: 1e-3 * (0.6 ** global_epoch))
                ]
            )
            checkpoint_predictions.append(model.predict(x_test, batch_size=2048)[0].flatten())
            weights.append(2 ** global_epoch)

    print('start predict...')
    predictions = np.average(checkpoint_predictions, weights=weights, axis=0)

    submission = pd.DataFrame.from_dict({
        'id': test['id'],
        'prediction': predictions
    })

    submission.to_csv('submission.csv', index=False)

if __name__ == '__main__':
    main()
